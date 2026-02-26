import base64
import hashlib
import json
import secrets
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs

from app import create_app
from auth.jwt_utils import JWT_CONVEX_AUDIENCE, create_mobile_access_token, verify_token
from config import Config
from db import mobile as mobile_db
from mobile import service


class MobileApiTests(unittest.TestCase):
    def setUp(self):
        self._config_backup = {
            "MAIN_DB_PATH": Config.MAIN_DB_PATH,
            "SESSIONS_DB_PATH": Config.SESSIONS_DB_PATH,
            "BACKEND_URL": Config.BACKEND_URL,
            "FRONTEND_URL": Config.FRONTEND_URL,
            "MOBILE_TOKEN_HASH_SECRET": Config.MOBILE_TOKEN_HASH_SECRET,
            "MOBILE_ALLOWED_REDIRECT_URIS": list(Config.MOBILE_ALLOWED_REDIRECT_URIS),
            "MOBILE_SCHOOLOGY_REQUEST_TTL_SECONDS": Config.MOBILE_SCHOOLOGY_REQUEST_TTL_SECONDS,
            "SCHOOLOGY_CONSUMER_KEY": Config.SCHOOLOGY_CONSUMER_KEY,
            "SCHOOLOGY_CONSUMER_SECRET": Config.SCHOOLOGY_CONSUMER_SECRET,
            "RATELIMIT_STORAGE_URI": Config.RATELIMIT_STORAGE_URI,
        }

        self._tempdir = tempfile.TemporaryDirectory()
        Config.MAIN_DB_PATH = f"{self._tempdir.name}/main.db"
        Config.SESSIONS_DB_PATH = f"{self._tempdir.name}/sessions.db"
        Config.BACKEND_URL = "http://localhost:3111"
        Config.FRONTEND_URL = "http://localhost:3112"
        Config.MOBILE_TOKEN_HASH_SECRET = "test-mobile-hash-secret"
        Config.MOBILE_ALLOWED_REDIRECT_URIS = ["pinewoodone://auth/callback"]
        Config.MOBILE_SCHOOLOGY_REQUEST_TTL_SECONDS = 300
        Config.SCHOOLOGY_CONSUMER_KEY = "test-schoology-key"
        Config.SCHOOLOGY_CONSUMER_SECRET = "test-schoology-secret"
        Config.RATELIMIT_STORAGE_URI = "memory://"

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        for key, value in self._config_backup.items():
            setattr(Config, key, value)
        self._tempdir.cleanup()

    def _create_user(self, email="student@pinewood.edu", name="Student"):
        conn = sqlite3.connect(Config.MAIN_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (google_user_id, email, name, last_login) VALUES (?, ?, ?, ?)",
            (f"google-{secrets.token_hex(4)}", email, name, mobile_db.to_db_time(mobile_db.utcnow())),
        )
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return {"id": user_id, "email": email, "name": name}

    def _pkce_challenge(self, verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def _mobile_auth_header(self, user: dict, device_id: str):
        token = create_mobile_access_token(
            user_id=user["id"],
            email=user["email"],
            name=user["name"],
            device_id=device_id,
        )
        return {"Authorization": f"Bearer {token}"}

    def _insert_mobile_auth_code(self, user_id: int, provider: str, device_id: str, challenge: str, ttl_seconds: int = 120):
        one_time_code = secrets.token_urlsafe(32)
        mobile_db.insert_mobile_auth_code(
            code_hash=service.hash_opaque_token(one_time_code),
            user_id=user_id,
            expires_at=mobile_db.utcnow() + timedelta(seconds=ttl_seconds),
            provider=provider,
            redirect_uri="pinewoodone://auth/callback",
            state_nonce=json.dumps(
                {
                    "nonce": "n",
                    "device_id": device_id,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "client_state": "abc",
                }
            ),
        )
        return one_time_code

    def test_exchange_success_then_replay_invalid_grant(self):
        user = self._create_user()
        verifier = "A" * 43
        challenge = self._pkce_challenge(verifier)
        one_time_code = self._insert_mobile_auth_code(
            user_id=user["id"],
            provider="google",
            device_id="device-1",
            challenge=challenge,
        )

        resp = self.client.post(
            "/api/mobile/v1/auth/exchange",
            json={
                "code": one_time_code,
                "code_verifier": verifier,
                "device_id": "device-1",
                "platform": "ios",
                "app_version": "1.0.0",
            },
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertIn("access_token", payload)
        self.assertIn("refresh_token", payload)

        replay = self.client.post(
            "/api/mobile/v1/auth/exchange",
            json={
                "code": one_time_code,
                "code_verifier": verifier,
                "device_id": "device-1",
                "platform": "ios",
                "app_version": "1.0.0",
            },
        )
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.get_json()["error"], "invalid_grant")

    def test_exchange_rejects_schoology_code_on_google_exchange_endpoint(self):
        user = self._create_user()
        verifier = "A" * 43
        challenge = self._pkce_challenge(verifier)
        one_time_code = self._insert_mobile_auth_code(
            user_id=user["id"],
            provider="schoology",
            device_id="device-1",
            challenge=challenge,
        )

        resp = self.client.post(
            "/api/mobile/v1/auth/exchange",
            json={
                "code": one_time_code,
                "code_verifier": verifier,
                "device_id": "device-1",
                "platform": "ios",
                "app_version": "1.0.0",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "invalid_grant")

    def test_refresh_rotation_and_reuse_detection(self):
        user = self._create_user()
        now = mobile_db.utcnow()
        refresh = secrets.token_urlsafe(64)
        mobile_db.insert_mobile_refresh_token(
            user_id=user["id"],
            token_hash=service.hash_opaque_token(refresh),
            device_id="device-1",
            issued_at=now,
            expires_at=now + timedelta(days=30),
        )

        first = self.client.post(
            "/api/mobile/v1/auth/refresh",
            json={"refresh_token": refresh, "device_id": "device-1"},
        )
        self.assertEqual(first.status_code, 200)

        replay = self.client.post(
            "/api/mobile/v1/auth/refresh",
            json={"refresh_token": refresh, "device_id": "device-1"},
        )
        self.assertEqual(replay.status_code, 401)
        self.assertEqual(replay.get_json()["error"], "reuse_detected")

    def test_mobile_convex_token_uses_convex_audience(self):
        user = self._create_user()
        headers = self._mobile_auth_header(user, "device-1")

        resp = self.client.get("/api/mobile/v1/convex/token", headers=headers)
        self.assertEqual(resp.status_code, 200)

        token = resp.get_json()["token"]
        payload = verify_token(token, JWT_CONVEX_AUDIENCE)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["aud"], "convex")

    def test_web_session_ticket_bootstrap_is_single_use(self):
        user = self._create_user()
        headers = self._mobile_auth_header(user, "device-1")

        ticket_resp = self.client.post(
            "/api/mobile/v1/web/session-ticket",
            headers=headers,
            json={"device_id": "device-1"},
        )
        self.assertEqual(ticket_resp.status_code, 200)
        ticket = ticket_resp.get_json()["ticket"]

        bad_redirect = self.client.get(
            "/api/mobile/v1/web/session/bootstrap",
            query_string={"ticket": ticket, "redirect": "https://evil.example/mobile/onboarding"},
        )
        self.assertEqual(bad_redirect.status_code, 400)
        self.assertEqual(bad_redirect.get_json()["error"], "invalid_redirect")

        ok = self.client.get(
            "/api/mobile/v1/web/session/bootstrap",
            query_string={"ticket": ticket, "redirect": "http://localhost:3112/mobile/onboarding"},
        )
        self.assertEqual(ok.status_code, 302)

        replay = self.client.get(
            "/api/mobile/v1/web/session/bootstrap",
            query_string={"ticket": ticket, "redirect": "http://localhost:3112/mobile/onboarding"},
        )
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.get_json()["error"], "invalid_ticket")

    def test_device_register_is_idempotent_and_unregister_revokes_device_tokens(self):
        user = self._create_user()
        headers = self._mobile_auth_header(user, "device-1")

        body = {
            "device_id": "device-1",
            "platform": "ios",
            "app_version": "1.0.0",
            "locale": "en-US",
            "timezone": "America/Los_Angeles",
        }
        first = self.client.post("/api/mobile/v1/devices/register", headers=headers, json=body)
        second = self.client.post("/api/mobile/v1/devices/register", headers=headers, json=body)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        refresh = secrets.token_urlsafe(64)
        now = mobile_db.utcnow()
        mobile_db.insert_mobile_refresh_token(
            user_id=user["id"],
            token_hash=service.hash_opaque_token(refresh),
            device_id="device-1",
            issued_at=now,
            expires_at=now + timedelta(days=30),
        )

        remove_resp = self.client.delete(
            "/api/mobile/v1/devices/register",
            headers=headers,
            json={"device_id": "device-1"},
        )
        self.assertEqual(remove_resp.status_code, 204)

        refresh_resp = self.client.post(
            "/api/mobile/v1/auth/refresh",
            json={"refresh_token": refresh, "device_id": "device-1"},
        )
        self.assertEqual(refresh_resp.status_code, 401)

    def test_mobile_schoology_start_rejects_invalid_pkce_challenge(self):
        user = self._create_user()
        headers = self._mobile_auth_header(user, "device-1")

        resp = self.client.post(
            "/api/mobile/v1/auth/schoology/start",
            headers=headers,
            json={
                "redirect_uri": "pinewoodone://auth/callback",
                "device_id": "device-1",
                "code_challenge": ("A" * 42) + ".",
                "code_challenge_method": "S256",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "invalid_request")

    @patch("mobile.service.update_onboarding_step")
    @patch("mobile.service.update_schoology_connected")
    @patch("mobile.service.complete_oauth")
    @patch("mobile.service.start_oauth")
    def test_mobile_schoology_callback_and_exchange_success(
        self,
        mock_start_oauth,
        mock_complete_oauth,
        _mock_update_connected,
        _mock_update_step,
    ):
        user = self._create_user()
        headers = self._mobile_auth_header(user, "device-1")
        verifier = "A" * 43
        challenge = self._pkce_challenge(verifier)
        request_token = "requesttoken1234567890ABCDE"
        mock_start_oauth.return_value = ("https://schoology.example/auth", request_token, "request-secret")
        mock_complete_oauth.return_value = ("access-token", "access-secret")

        start_resp = self.client.post(
            "/api/mobile/v1/auth/schoology/start",
            headers=headers,
            json={
                "redirect_uri": "pinewoodone://auth/callback",
                "device_id": "device-1",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "client-state-123",
            },
        )
        self.assertEqual(start_resp.status_code, 200)
        self.assertIn("auth_url", start_resp.get_json())

        callback_url = mock_start_oauth.call_args.kwargs["callback_url"]
        callback_state = parse_qs(urlparse(callback_url).query)["state"][0]

        callback_resp = self.client.get(
            "/api/mobile/v1/auth/schoology/callback",
            query_string={
                "state": callback_state,
                "oauth_token": request_token,
            },
        )
        self.assertEqual(callback_resp.status_code, 302)
        redirect_location = callback_resp.headers["Location"]
        parsed_redirect = urlparse(redirect_location)
        query = parse_qs(parsed_redirect.query)
        self.assertEqual(f"{parsed_redirect.scheme}://{parsed_redirect.netloc}{parsed_redirect.path}", "pinewoodone://auth/callback")
        self.assertIn("code", query)
        self.assertEqual(query["state"][0], "client-state-123")
        one_time_code = query["code"][0]

        exchange_resp = self.client.post(
            "/api/mobile/v1/auth/schoology/exchange",
            headers=headers,
            json={
                "code": one_time_code,
                "code_verifier": verifier,
                "device_id": "device-1",
            },
        )
        self.assertEqual(exchange_resp.status_code, 200)
        self.assertEqual(
            exchange_resp.get_json(),
            {
                "success": True,
                "schoology_connected": True,
                "onboarding_step": "smart_consent",
            },
        )

    def test_mobile_schoology_callback_error_redirect_shape(self):
        state_token = service.create_mobile_state(
            redirect_uri="pinewoodone://auth/callback",
            device_id="device-1",
            code_challenge=self._pkce_challenge("A" * 43),
            code_challenge_method="S256",
            client_state="client-state-abc",
            flow="schoology",
            user_id=123,
        )
        resp = self.client.get(
            "/api/mobile/v1/auth/schoology/callback",
            query_string={"state": state_token, "error": "access_denied"},
        )
        self.assertEqual(resp.status_code, 302)
        location = resp.headers["Location"]
        parsed = urlparse(location)
        query = parse_qs(parsed.query)
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", "pinewoodone://auth/callback")
        self.assertEqual(query["error"][0], "access_denied")
        self.assertEqual(query["state"][0], "client-state-abc")

    def test_mobile_schoology_exchange_rejects_wrong_provider_and_wrong_device(self):
        user = self._create_user()
        headers = self._mobile_auth_header(user, "device-1")
        verifier = "A" * 43
        challenge = self._pkce_challenge(verifier)

        google_code = self._insert_mobile_auth_code(
            user_id=user["id"],
            provider="google",
            device_id="device-1",
            challenge=challenge,
        )
        wrong_provider_resp = self.client.post(
            "/api/mobile/v1/auth/schoology/exchange",
            headers=headers,
            json={
                "code": google_code,
                "code_verifier": verifier,
                "device_id": "device-1",
            },
        )
        self.assertEqual(wrong_provider_resp.status_code, 400)
        self.assertEqual(wrong_provider_resp.get_json()["error"], "invalid_grant")

        schoology_code = self._insert_mobile_auth_code(
            user_id=user["id"],
            provider="schoology",
            device_id="device-2",
            challenge=challenge,
        )
        wrong_device_resp = self.client.post(
            "/api/mobile/v1/auth/schoology/exchange",
            headers=headers,
            json={
                "code": schoology_code,
                "code_verifier": verifier,
                "device_id": "device-1",
            },
        )
        self.assertEqual(wrong_device_resp.status_code, 409)
        self.assertEqual(wrong_device_resp.get_json()["error"], "device_mismatch")

    @patch("mobile.service.start_oauth")
    def test_mobile_schoology_start_is_rate_limited(self, mock_start_oauth):
        user = self._create_user()
        headers = self._mobile_auth_header(user, "device-1")
        def _start_oauth_side_effect(*_args, **_kwargs):
            token = f"requesttoken-{secrets.token_hex(12)}"
            return ("https://schoology.example/auth", token, "request-secret")

        mock_start_oauth.side_effect = _start_oauth_side_effect
        body = {
            "redirect_uri": "pinewoodone://auth/callback",
            "device_id": "device-1",
            "code_challenge": self._pkce_challenge("A" * 43),
            "code_challenge_method": "S256",
        }

        for _ in range(10):
            response = self.client.post("/api/mobile/v1/auth/schoology/start", headers=headers, json=body)
            self.assertEqual(response.status_code, 200)

        limited = self.client.post("/api/mobile/v1/auth/schoology/start", headers=headers, json=body)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.get_json(), {"error": "rate_limited"})

    def test_rate_limit_handler_returns_machine_readable_json(self):
        for _ in range(10):
            response = self.client.get("/api/mobile/v1/auth/google/start")
            self.assertEqual(response.status_code, 400)

        limited = self.client.get("/api/mobile/v1/auth/google/start")
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.get_json(), {"error": "rate_limited"})

    def test_google_start_rejects_non_base64url_pkce_challenge(self):
        bad_challenge = ("A" * 42) + "."
        response = self.client.get(
            "/api/mobile/v1/auth/google/start",
            query_string={
                "redirect_uri": "pinewoodone://auth/callback",
                "device_id": "device-1",
                "code_challenge": bad_challenge,
                "code_challenge_method": "S256",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
