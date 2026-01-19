"""
JWT utilities for Convex authentication using RS256
"""
import os
import json
import base64
import jwt
from datetime import datetime, timedelta
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from config import Config

# JWT configuration
JWT_ALGORITHM = "RS256"
JWT_EXPIRATION_HOURS = 24
JWT_ISSUER = os.environ.get("JWT_ISSUER", Config.BACKEND_URL)
JWT_AUDIENCE = "convex"
JWT_KEY_ID = "pinewood-one-key-1"

# RSA key paths
PRIVATE_KEY_PATH = os.path.join(os.path.dirname(__file__), "..", "keys", "private.pem")
PUBLIC_KEY_PATH = os.path.join(os.path.dirname(__file__), "..", "keys", "public.pem")


def _ensure_keys_exist():
    """Generate RSA keys if they don't exist."""
    keys_dir = os.path.dirname(PRIVATE_KEY_PATH)
    if not os.path.exists(keys_dir):
        os.makedirs(keys_dir)

    if not os.path.exists(PRIVATE_KEY_PATH) or not os.path.exists(PUBLIC_KEY_PATH):
        # Generate new RSA key pair
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        public_key = private_key.public_key()

        # Save private key
        with open(PRIVATE_KEY_PATH, "wb") as f:
            f.write(private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ))

        # Save public key
        with open(PUBLIC_KEY_PATH, "wb") as f:
            f.write(public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            ))

        print("Generated new RSA key pair for JWT signing")


def _load_private_key():
    """Load the RSA private key."""
    _ensure_keys_exist()
    with open(PRIVATE_KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def _load_public_key():
    """Load the RSA public key."""
    _ensure_keys_exist()
    with open(PUBLIC_KEY_PATH, "rb") as f:
        return serialization.load_pem_public_key(f.read())


def _int_to_base64url(n: int) -> str:
    """Convert an integer to base64url encoding."""
    byte_length = (n.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(n.to_bytes(byte_length, "big")).rstrip(b"=").decode("ascii")


def get_jwks() -> dict:
    """
    Get the JSON Web Key Set for the public key.
    This is used by Convex to verify JWT signatures.
    """
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


def create_convex_token(user_id: int, email: str, name: str) -> str:
    """
    Create a JWT token for Convex authentication.

    Args:
        user_id: The internal user ID
        email: User's email address
        name: User's display name

    Returns:
        JWT token string
    """
    private_key = _load_private_key()

    now = datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "email": email,
        "name": name,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRATION_HOURS)
    }

    headers = {
        "kid": JWT_KEY_ID,
        "typ": "JWT",
        "alg": JWT_ALGORITHM
    }

    return jwt.encode(payload, private_key, algorithm=JWT_ALGORITHM, headers=headers)


def verify_convex_token(token: str) -> dict | None:
    """
    Verify and decode a JWT token.

    Args:
        token: JWT token string

    Returns:
        Decoded payload dict or None if invalid
    """
    try:
        public_key = _load_public_key()
        return jwt.decode(
            token,
            public_key,
            algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER
        )
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
