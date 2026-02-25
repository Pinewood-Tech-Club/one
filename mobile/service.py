"""
Service layer for mobile auth/session flows.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import requests
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from auth.google import get_user_info
from auth.jwt_utils import create_convex_token, create_mobile_access_token
from config import Config
from db import mobile as mobile_db
from db.users import get_or_create_user, get_user_by_id
from onboarding import get_or_create_user as convex_get_or_create_user

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
PKCE_ALLOWED_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9\-_=.]{20,512}$")


class MobileAuthError(Exception):
    def __init__(self, code: str, status_code: int = 400, message: str | None = None):
        self.code = code
        self.status_code = status_code
        self.message = message or code
        super().__init__(self.message)


def _state_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(Config.SECRET_KEY, salt="mobile-oauth-state-v1")


def now_utc():
    return mobile_db.utcnow()


def _token_hash_secret() -> str:
    return Config.MOBILE_TOKEN_HASH_SECRET or Config.SECRET_KEY


def hash_opaque_token(token: str) -> str:
    digest = hmac.new(
        _token_hash_secret().encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


def _add_query(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_params.update({k: v for k, v in params.items() if v is not None})
    return urlunparse(parsed._replace(query=urlencode(query_params)))


def fallback_mobile_redirect_uri() -> str:
    if Config.MOBILE_ALLOWED_REDIRECT_URIS:
        return Config.MOBILE_ALLOWED_REDIRECT_URIS[0]
    return "pinewoodone://auth/callback"


def is_allowed_mobile_redirect_uri(redirect_uri: str) -> bool:
    return redirect_uri in Config.MOBILE_ALLOWED_REDIRECT_URIS


def validate_device_id(device_id: str) -> bool:
    return bool(device_id and DEVICE_ID_RE.fullmatch(device_id))


def validate_pkce_challenge(challenge: str, method: str) -> bool:
    if method != "S256":
        return False
    return bool(challenge and PKCE_ALLOWED_RE.fullmatch(challenge))


def validate_pkce_verifier(verifier: str) -> bool:
    return bool(verifier and PKCE_ALLOWED_RE.fullmatch(verifier))


def validate_opaque_input(value: str) -> bool:
    return bool(value and OPAQUE_TOKEN_RE.fullmatch(value))


def create_mobile_state(
    redirect_uri: str,
    device_id: str,
    code_challenge: str,
    code_challenge_method: str,
    client_state: str | None,
) -> str:
    payload = {
        "nonce": secrets.token_hex(12),
        "redirect_uri": redirect_uri,
        "device_id": device_id,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "client_state": client_state,
        "iat": int(now_utc().timestamp()),
    }
    return _state_serializer().dumps(payload)


def parse_mobile_state(state_token: str) -> dict:
    try:
        return _state_serializer().loads(
            state_token,
            max_age=Config.MOBILE_STATE_MAX_AGE_SECONDS,
        )
    except SignatureExpired as exc:
        raise MobileAuthError("invalid_state", 400) from exc
    except BadSignature as exc:
        raise MobileAuthError("invalid_state", 400) from exc


def get_mobile_google_start_redirect(
    redirect_uri: str,
    device_id: str,
    code_challenge: str,
    code_challenge_method: str,
    client_state: str | None,
) -> str:
    if not is_allowed_mobile_redirect_uri(redirect_uri):
        raise MobileAuthError("invalid_request", 400)
    if not validate_device_id(device_id):
        raise MobileAuthError("invalid_request", 400)
    if not validate_pkce_challenge(code_challenge, code_challenge_method):
        raise MobileAuthError("invalid_request", 400)

    state_token = create_mobile_state(
        redirect_uri=redirect_uri,
        device_id=device_id,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        client_state=client_state,
    )

    google_query = {
        "client_id": Config.GOOGLE_CLIENT_ID,
        "redirect_uri": f"{Config.BACKEND_URL}/api/mobile/v1/auth/google/callback",
        "response_type": "code",
        "scope": "email profile",
        "hd": "pinewood.edu",
        "state": state_token,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(google_query)}"


def _exchange_google_code_for_token(code: str) -> dict:
    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": Config.GOOGLE_CLIENT_ID,
            "client_secret": Config.GOOGLE_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{Config.BACKEND_URL}/api/mobile/v1/auth/google/callback",
        },
        timeout=15,
    )
    return response.json()


def process_google_callback(code: str, state_token: str) -> tuple[str, str | None]:
    state_data = parse_mobile_state(state_token)
    redirect_uri = state_data.get("redirect_uri")
    if not redirect_uri or not is_allowed_mobile_redirect_uri(redirect_uri):
        raise MobileAuthError("invalid_state", 400)

    token_response = _exchange_google_code_for_token(code)
    if "error" in token_response or "access_token" not in token_response:
        raise MobileAuthError("token_failed", 401)

    user_response = get_user_info(token_response["access_token"])
    if "error" in user_response or "email" not in user_response:
        raise MobileAuthError("user_info_failed", 401)

    if user_response.get("hd") != "pinewood.edu":
        raise MobileAuthError("invalid_domain", 401)

    email = user_response["email"]
    google_user_id = user_response.get("id", "")
    name = user_response.get("name", email.split("@")[0])
    user_id = get_or_create_user(google_user_id, email, name)

    try:
        convex_get_or_create_user(Config.CONVEX_URL, str(user_id))
    except Exception:
        # Convex bootstrap is best effort for mobile auth.
        pass

    one_time_code = secrets.token_urlsafe(32)
    one_time_code_hash = hash_opaque_token(one_time_code)
    expires_at = now_utc() + timedelta(seconds=Config.MOBILE_AUTH_CODE_TTL_SECONDS)

    mobile_db.insert_mobile_auth_code(
        code_hash=one_time_code_hash,
        user_id=user_id,
        expires_at=expires_at,
        provider="google",
        redirect_uri=redirect_uri,
        state_nonce=json.dumps(
            {
                "nonce": state_data.get("nonce"),
                "device_id": state_data.get("device_id"),
                "code_challenge": state_data.get("code_challenge"),
                "code_challenge_method": state_data.get("code_challenge_method"),
                "client_state": state_data.get("client_state"),
            }
        ),
    )

    return one_time_code, state_data.get("client_state")


def build_mobile_callback_redirect(redirect_uri: str, code: str | None = None, error: str | None = None, state: str | None = None) -> str:
    params: dict[str, str] = {}
    if code is not None:
        params["code"] = code
    if error is not None:
        params["error"] = error
    if state is not None:
        params["state"] = state
    return _add_query(redirect_uri, params)


def _pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def exchange_auth_code(
    code: str,
    code_verifier: str,
    device_id: str,
    platform: str,
    app_version: str,
    locale: str | None,
    timezone_value: str | None,
) -> dict:
    if not validate_opaque_input(code) or not validate_pkce_verifier(code_verifier):
        raise MobileAuthError("invalid_grant", 400)
    if not validate_device_id(device_id):
        raise MobileAuthError("invalid_grant", 400)
    if not platform or not app_version:
        raise MobileAuthError("invalid_request", 400)

    now = now_utc()
    code_hash = hash_opaque_token(code)
    status, code_row = mobile_db.consume_mobile_auth_code(code_hash, now)
    if status != "ok" or not code_row:
        raise MobileAuthError("invalid_grant", 400)

    state_data = json.loads(code_row["state_nonce"])
    if state_data.get("device_id") != device_id:
        raise MobileAuthError("device_mismatch", 409)

    if state_data.get("code_challenge_method") != "S256":
        raise MobileAuthError("invalid_grant", 400)

    if _pkce_s256(code_verifier) != state_data.get("code_challenge"):
        raise MobileAuthError("invalid_grant", 400)

    user = get_user_by_id(code_row["user_id"])
    if not user:
        raise MobileAuthError("unauthorized", 401)

    access_token = create_mobile_access_token(
        user_id=user["id"],
        email=user["email"],
        name=user["name"],
        device_id=device_id,
    )

    refresh_token = secrets.token_urlsafe(64)
    refresh_hash = hash_opaque_token(refresh_token)
    refresh_expires_at = now + timedelta(days=Config.MOBILE_REFRESH_TOKEN_TTL_DAYS)

    mobile_db.insert_mobile_refresh_token(
        user_id=user["id"],
        token_hash=refresh_hash,
        device_id=device_id,
        issued_at=now,
        expires_at=refresh_expires_at,
    )
    mobile_db.upsert_mobile_device(
        user_id=user["id"],
        device_id=device_id,
        platform=platform,
        app_version=app_version,
        push_token=None,
        push_env=None,
        locale=locale,
        timezone_value=timezone_value,
        now=now,
    )

    return {
        "access_token": access_token,
        "expires_in": Config.MOBILE_ACCESS_TOKEN_TTL_SECONDS,
        "refresh_token": refresh_token,
        "refresh_expires_in": Config.MOBILE_REFRESH_TOKEN_TTL_DAYS * 24 * 3600,
        "token_type": "Bearer",
        "user": {
            "user_id": user["id"],
            "email": user["email"],
            "name": user["name"],
        },
    }


def refresh_mobile_tokens(refresh_token: str, device_id: str) -> dict:
    if not validate_opaque_input(refresh_token) or not validate_device_id(device_id):
        raise MobileAuthError("invalid_token", 401)

    now = now_utc()
    old_hash = hash_opaque_token(refresh_token)
    new_token = secrets.token_urlsafe(64)
    new_hash = hash_opaque_token(new_token)
    new_expiry = now + timedelta(days=Config.MOBILE_REFRESH_TOKEN_TTL_DAYS)

    status, row = mobile_db.rotate_mobile_refresh_token(
        token_hash=old_hash,
        new_token_hash=new_hash,
        request_device_id=device_id,
        now=now,
        new_expires_at=new_expiry,
    )

    if status == "reuse_detected":
        raise MobileAuthError("reuse_detected", 401)
    if status in {"invalid", "device_mismatch"}:
        raise MobileAuthError("invalid_token", 401)
    if status != "ok" or not row:
        raise MobileAuthError("invalid_token", 401)

    user = get_user_by_id(row["user_id"])
    if not user:
        raise MobileAuthError("invalid_token", 401)

    access_token = create_mobile_access_token(
        user_id=user["id"],
        email=user["email"],
        name=user["name"],
        device_id=row["device_id"],
    )

    return {
        "access_token": access_token,
        "expires_in": Config.MOBILE_ACCESS_TOKEN_TTL_SECONDS,
        "refresh_token": new_token,
        "refresh_expires_in": Config.MOBILE_REFRESH_TOKEN_TTL_DAYS * 24 * 3600,
        "token_type": "Bearer",
        "user": {
            "user_id": user["id"],
            "email": user["email"],
            "name": user["name"],
        },
    }


def logout_mobile_session(
    user_id: int,
    token_payload: dict,
    refresh_token: str | None,
    all_devices: bool,
):
    now = now_utc()
    if all_devices:
        mobile_db.revoke_mobile_refresh_tokens_for_user(user_id, now)
        return

    if refresh_token and validate_opaque_input(refresh_token):
        mobile_db.revoke_mobile_refresh_token_for_user(hash_opaque_token(refresh_token), user_id, now)
        return

    device_id = token_payload.get("device_id")
    if isinstance(device_id, str) and validate_device_id(device_id):
        mobile_db.revoke_mobile_refresh_tokens_for_device(user_id, device_id, now)


def create_mobile_convex_token(user: dict) -> dict:
    token = create_convex_token(
        user_id=user["id"],
        email=user["email"],
        name=user["name"],
        expires_in_seconds=300,
    )
    return {
        "token": token,
        "expires_in": 300,
    }


def create_web_session_ticket(user_id: int, device_id: str) -> dict:
    if not validate_device_id(device_id):
        raise MobileAuthError("invalid_request", 400)

    now = now_utc()
    expires_at = now + timedelta(seconds=Config.MOBILE_WEB_TICKET_TTL_SECONDS)
    ticket = secrets.token_urlsafe(32)
    ticket_hash = hash_opaque_token(ticket)

    mobile_db.insert_mobile_web_ticket(
        ticket_hash=ticket_hash,
        user_id=user_id,
        device_id=device_id,
        expires_at=expires_at,
    )

    return {
        "ticket": ticket,
        "expires_in": Config.MOBILE_WEB_TICKET_TTL_SECONDS,
    }


def consume_web_session_ticket(ticket: str) -> tuple[str, dict | None]:
    if not validate_opaque_input(ticket):
        return "invalid", None
    return mobile_db.consume_mobile_web_ticket(hash_opaque_token(ticket), now_utc())


def is_valid_bootstrap_redirect(redirect_url: str) -> bool:
    try:
        parsed = urlparse(redirect_url)
        frontend = urlparse(Config.FRONTEND_URL)
        return (
            parsed.scheme == frontend.scheme
            and parsed.netloc == frontend.netloc
            and parsed.path.startswith("/mobile/onboarding")
        )
    except Exception:
        return False


def register_mobile_device(
    user_id: int,
    device_id: str,
    platform: str,
    app_version: str,
    push_token: str | None,
    push_env: str | None,
    locale: str | None,
    timezone_value: str | None,
):
    if not validate_device_id(device_id) or not platform or not app_version:
        raise MobileAuthError("invalid_request", 400)

    mobile_db.upsert_mobile_device(
        user_id=user_id,
        device_id=device_id,
        platform=platform,
        app_version=app_version,
        push_token=push_token,
        push_env=push_env,
        locale=locale,
        timezone_value=timezone_value,
        now=now_utc(),
    )


def unregister_mobile_device(user_id: int, device_id: str):
    if not validate_device_id(device_id):
        raise MobileAuthError("invalid_request", 400)
    now = now_utc()
    mobile_db.revoke_mobile_device(user_id=user_id, device_id=device_id, now=now)
    mobile_db.revoke_mobile_refresh_tokens_for_device(user_id=user_id, device_id=device_id, now=now)
