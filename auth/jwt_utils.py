"""
JWT utilities for backend-issued RS256 tokens.
"""
import os
import base64
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from config import Config

# JWT configuration
JWT_ALGORITHM = "RS256"
JWT_ISSUER = os.environ.get("JWT_ISSUER", Config.BACKEND_URL)
JWT_KEY_ID = "pinewood-one-key-1"
JWT_CONVEX_AUDIENCE = "convex"
JWT_MOBILE_AUDIENCE = "mobile_api"
JWT_DEFAULT_CONVEX_EXPIRATION_HOURS = 24

# RSA key paths
PRIVATE_KEY_PATH = os.path.join(os.path.dirname(__file__), "..", "keys", "private.pem")
PUBLIC_KEY_PATH = os.path.join(os.path.dirname(__file__), "..", "keys", "public.pem")


def _ensure_keys_exist():
    """Generate RSA keys if they don't exist."""
    keys_dir = os.path.dirname(PRIVATE_KEY_PATH)
    if not os.path.exists(keys_dir):
        os.makedirs(keys_dir)

    if not os.path.exists(PRIVATE_KEY_PATH) or not os.path.exists(PUBLIC_KEY_PATH):
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )
        public_key = private_key.public_key()

        with open(PRIVATE_KEY_PATH, "wb") as f:
            f.write(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )

        with open(PUBLIC_KEY_PATH, "wb") as f:
            f.write(
                public_key.public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            )

        print("Generated new RSA key pair for JWT signing")


def _load_private_key():
    _ensure_keys_exist()
    with open(PRIVATE_KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def _load_public_key():
    _ensure_keys_exist()
    with open(PUBLIC_KEY_PATH, "rb") as f:
        return serialization.load_pem_public_key(f.read())


def _int_to_base64url(n: int) -> str:
    byte_length = (n.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(n.to_bytes(byte_length, "big")).rstrip(b"=").decode("ascii")


def get_jwks() -> dict:
    """Get JSON Web Key Set for public key verification."""
    public_key = _load_public_key()
    public_numbers = public_key.public_numbers()

    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": JWT_ALGORITHM,
                "kid": JWT_KEY_ID,
                "n": _int_to_base64url(public_numbers.n),
                "e": _int_to_base64url(public_numbers.e),
            }
        ]
    }


def create_token(
    user_id: int,
    email: str,
    name: str,
    audience: str,
    expires_in_seconds: int | None = None,
    extra_claims: dict | None = None,
) -> str:
    """Create a signed JWT for the given audience."""
    private_key = _load_private_key()

    if expires_in_seconds is None:
        if audience == JWT_CONVEX_AUDIENCE:
            expires_in_seconds = JWT_DEFAULT_CONVEX_EXPIRATION_HOURS * 3600
        else:
            expires_in_seconds = Config.MOBILE_ACCESS_TOKEN_TTL_SECONDS

    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "name": name,
        "iss": JWT_ISSUER,
        "aud": audience,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in_seconds),
    }

    if extra_claims:
        payload.update(extra_claims)

    headers = {
        "kid": JWT_KEY_ID,
        "typ": "JWT",
        "alg": JWT_ALGORITHM,
    }

    return jwt.encode(payload, private_key, algorithm=JWT_ALGORITHM, headers=headers)


def create_convex_token(
    user_id: int,
    email: str,
    name: str,
    expires_in_seconds: int | None = None,
) -> str:
    """Backwards-compatible helper for Convex JWT creation."""
    return create_token(
        user_id=user_id,
        email=email,
        name=name,
        audience=JWT_CONVEX_AUDIENCE,
        expires_in_seconds=expires_in_seconds,
    )


def create_mobile_access_token(
    user_id: int,
    email: str,
    name: str,
    device_id: str,
    expires_in_seconds: int | None = None,
) -> str:
    """Create a mobile API access token."""
    return create_token(
        user_id=user_id,
        email=email,
        name=name,
        audience=JWT_MOBILE_AUDIENCE,
        expires_in_seconds=expires_in_seconds or Config.MOBILE_ACCESS_TOKEN_TTL_SECONDS,
        extra_claims={
            "device_id": device_id,
            "jti": secrets.token_hex(16),
        },
    )


def verify_token(token: str, audience: str) -> dict | None:
    """Verify and decode a JWT for the expected audience."""
    try:
        public_key = _load_public_key()
        return jwt.decode(
            token,
            public_key,
            algorithms=[JWT_ALGORITHM],
            audience=audience,
            issuer=JWT_ISSUER,
        )
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def verify_convex_token(token: str) -> dict | None:
    """Backwards-compatible helper for Convex JWT verification."""
    return verify_token(token, audience=JWT_CONVEX_AUDIENCE)


def verify_mobile_access_token(token: str) -> dict | None:
    """Verify a mobile access token."""
    return verify_token(token, audience=JWT_MOBILE_AUDIENCE)
