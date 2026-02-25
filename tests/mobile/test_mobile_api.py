import base64
import hashlib
import json
import secrets
import sqlite3
import tempfile
import unittest
from datetime import timedelta

from app import create_app
from auth.jwt_utils import JWT_CONVEX_AUDIENCE, create_mobile_access_token, verify_token
from config import Config
from db import mobile as mobile_db
from mobile import service


class MobileApiTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        Config.MAIN_DB_PATH = f"{self._tempdir.name}/main.db"
        Config.SESSIONS_DB_PATH = f"{self._tempdir.name}/sessions.db"
        Config.BACKEND_URL = "http://localhost:3111"
        Config.FRONTEND_URL = "http://localhost:3112"
        Config.MOBILE_TOKEN_HASH_SECRET = "test-mobile-hash-secret"
        Config.MOBILE_ALLOWED_REDIRECT_URIS = ["pinewoodone://auth/callback"]
        Config.RATELIMIT_STORAGE_URI = "memory://"

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
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

    def test_exchange_success_then_replay_invalid_grant(self):
        user = self._create_user()
        verifier = "A" * 43
        challenge = self._pkce_challenge(verifier)
        one_time_code = secrets.token_urlsafe(32)

        mobile_db.insert_mobile_auth_code(
            code_hash=service.hash_opaque_token(one_time_code),
            user_id=user["id"],
            expires_at=mobile_db.utcnow() + timedelta(seconds=120),
            provider="google",
            redirect_uri="pinewoodone://auth/callback",
            state_nonce=json.dumps(
                {
                    "nonce": "n",
                    "device_id": "device-1",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "client_state": "abc",
                }
            ),
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


if __name__ == "__main__":
    unittest.main()
