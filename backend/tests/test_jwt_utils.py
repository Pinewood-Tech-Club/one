"""
Tests for auth/jwt_utils.py — RS256 minting/verification for the Convex and
mobile audiences, plus the JWKS document.

Security-critical: covers expiry, signature tampering, payload tampering,
audience/issuer confusion, alg=none, and cross-key forgery.
"""
import base64
import json
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from auth import jwt_utils
from auth.jwt_utils import (
    JWT_CONVEX_AUDIENCE,
    JWT_ISSUER,
    JWT_KEY_ID,
    JWT_MOBILE_AUDIENCE,
    create_convex_token,
    create_mobile_access_token,
    create_token,
    get_jwks,
    verify_convex_token,
    verify_mobile_access_token,
    verify_token,
)
from tests.conftest import TEST_PRIVATE_KEY_PEM

USER = dict(user_id=42, email="student@pinewood.edu", name="Test Student")


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class TestMintAndVerify:
    def test_access_token_roundtrips_claims(self):
        token = create_token(**USER, audience=JWT_MOBILE_AUDIENCE)
        claims = verify_token(token, audience=JWT_MOBILE_AUDIENCE)
        assert claims is not None
        assert claims["sub"] == "42"
        assert claims["email"] == USER["email"]
        assert claims["name"] == USER["name"]
        assert claims["aud"] == JWT_MOBILE_AUDIENCE
        assert claims["iss"] == JWT_ISSUER
        assert claims["exp"] > claims["iat"]

    def test_header_pins_kid_and_alg(self):
        token = create_token(**USER, audience=JWT_MOBILE_AUDIENCE)
        header = pyjwt.get_unverified_header(token)
        assert header["alg"] == "RS256"
        assert header["kid"] == JWT_KEY_ID

    def test_convex_token_helpers(self):
        token = create_convex_token(**USER)
        claims = verify_convex_token(token)
        assert claims is not None
        assert claims["aud"] == JWT_CONVEX_AUDIENCE

    def test_mobile_access_token_carries_device_and_unique_jti(self):
        t1 = create_mobile_access_token(**USER, device_id="device-abc")
        t2 = create_mobile_access_token(**USER, device_id="device-abc")
        c1 = verify_mobile_access_token(t1)
        c2 = verify_mobile_access_token(t2)
        assert c1 is not None and c2 is not None
        assert c1["device_id"] == "device-abc"
        assert c1["jti"] != c2["jti"]  # jti must be unique per token


class TestRejection:
    def test_expired_token_rejected(self):
        token = create_token(**USER, audience=JWT_MOBILE_AUDIENCE, expires_in_seconds=-10)
        assert verify_token(token, audience=JWT_MOBILE_AUDIENCE) is None

    def test_tampered_signature_rejected(self):
        token = create_token(**USER, audience=JWT_MOBILE_AUDIENCE)
        header, payload, signature = token.split(".")
        bad_sig = ("A" if signature[0] != "A" else "B") + signature[1:]
        assert verify_token(f"{header}.{payload}.{bad_sig}", audience=JWT_MOBILE_AUDIENCE) is None

    def test_tampered_payload_rejected(self):
        token = create_token(**USER, audience=JWT_MOBILE_AUDIENCE)
        header, payload, signature = token.split(".")
        claims = json.loads(_b64url_decode(payload))
        claims["sub"] = "1"  # privilege escalation attempt
        forged_payload = _b64url_encode(json.dumps(claims).encode())
        assert verify_token(f"{header}.{forged_payload}.{signature}", audience=JWT_MOBILE_AUDIENCE) is None

    def test_wrong_audience_rejected(self):
        mobile_token = create_mobile_access_token(**USER, device_id="device-abc")
        convex_token = create_convex_token(**USER)
        # Cross-audience confusion must fail in both directions.
        assert verify_convex_token(mobile_token) is None
        assert verify_mobile_access_token(convex_token) is None

    def test_wrong_issuer_rejected(self):
        now = datetime.now(timezone.utc)
        token = pyjwt.encode(
            {
                "sub": "42",
                "iss": "https://evil.example.com",
                "aud": JWT_MOBILE_AUDIENCE,
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            TEST_PRIVATE_KEY_PEM,
            algorithm="RS256",
        )
        assert verify_token(token, audience=JWT_MOBILE_AUDIENCE) is None

    def test_alg_none_rejected(self):
        now = datetime.now(timezone.utc)
        header = _b64url_encode(json.dumps({"alg": "none", "typ": "JWT", "kid": JWT_KEY_ID}).encode())
        payload = _b64url_encode(
            json.dumps(
                {
                    "sub": "42",
                    "iss": JWT_ISSUER,
                    "aud": JWT_MOBILE_AUDIENCE,
                    "iat": int(now.timestamp()),
                    "exp": int((now + timedelta(minutes=5)).timestamp()),
                }
            ).encode()
        )
        assert verify_token(f"{header}.{payload}.", audience=JWT_MOBILE_AUDIENCE) is None

    def test_token_signed_with_attacker_key_rejected(self):
        attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        attacker_pem = attacker_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        now = datetime.now(timezone.utc)
        forged = pyjwt.encode(
            {
                "sub": "42",
                "iss": JWT_ISSUER,
                "aud": JWT_MOBILE_AUDIENCE,
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            attacker_pem,
            algorithm="RS256",
            headers={"kid": JWT_KEY_ID},
        )
        assert verify_token(forged, audience=JWT_MOBILE_AUDIENCE) is None

    def test_garbage_token_rejected(self):
        assert verify_token("not.a.jwt", audience=JWT_MOBILE_AUDIENCE) is None
        assert verify_token("", audience=JWT_MOBILE_AUDIENCE) is None


class TestJwks:
    def test_jwks_matches_signing_key(self):
        jwks = get_jwks()
        assert len(jwks["keys"]) == 1
        key = jwks["keys"][0]
        assert key["kty"] == "RSA"
        assert key["use"] == "sig"
        assert key["alg"] == "RS256"
        assert key["kid"] == JWT_KEY_ID

        public_numbers = jwt_utils._load_public_key().public_numbers()
        n = int.from_bytes(_b64url_decode(key["n"]), "big")
        e = int.from_bytes(_b64url_decode(key["e"]), "big")
        assert n == public_numbers.n
        assert e == public_numbers.e

    def test_jwks_key_verifies_minted_token(self):
        # A verifier that only has the JWKS must be able to verify our tokens.
        token = create_convex_token(**USER)
        jwks = get_jwks()
        public_key = pyjwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwks["keys"][0]))
        claims = pyjwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=JWT_CONVEX_AUDIENCE,
            issuer=JWT_ISSUER,
        )
        assert claims["sub"] == "42"
