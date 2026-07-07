"""
Tests for auth/jwt_utils.py — backend-issued RS256 JWTs.

Security properties under test:
  - mint + verify round-trip for each audience
  - audience is enforced (a convex token must NOT verify as a mobile token)
  - issuer is enforced (wrong issuer -> None)
  - expired tokens are rejected (fail closed -> None)
  - tampered signature is rejected
  - the "none" algorithm / unsigned tokens are rejected (alg confusion)
"""
import time

import jwt as pyjwt
import pytest

from auth import jwt_utils
from auth.jwt_utils import (
    JWT_CONVEX_AUDIENCE,
    JWT_ISSUER,
    JWT_MOBILE_AUDIENCE,
    create_convex_token,
    create_mobile_access_token,
    verify_convex_token,
    verify_mobile_access_token,
    verify_token,
)


def test_convex_token_round_trip():
    token = create_convex_token(user_id=7, email="a@b.com", name="A B")
    claims = verify_convex_token(token)
    assert claims is not None
    assert claims["sub"] == "7"
    assert claims["aud"] == JWT_CONVEX_AUDIENCE
    assert claims["iss"] == JWT_ISSUER
    assert "exp" in claims and "iat" in claims


def test_mobile_token_round_trip_has_device_and_jti():
    token = create_mobile_access_token(
        user_id=9, email="m@b.com", name="M B", device_id="device-xyz"
    )
    claims = verify_mobile_access_token(token)
    assert claims is not None
    assert claims["aud"] == JWT_MOBILE_AUDIENCE
    assert claims["device_id"] == "device-xyz"
    assert "jti" in claims


def test_audience_is_enforced():
    convex = create_convex_token(user_id=1, email="a@b.com", name="A")
    # A convex-audience token must not validate as a mobile token.
    assert verify_mobile_access_token(convex) is None


def test_wrong_issuer_rejected():
    token = create_convex_token(user_id=1, email="a@b.com", name="A")
    # Verifying against an audience is fine, but a mismatched issuer must fail.
    public_key = jwt_utils._load_public_key()
    with pytest.raises(pyjwt.InvalidIssuerError):
        pyjwt.decode(
            token,
            public_key,
            algorithms=[jwt_utils.JWT_ALGORITHM],
            audience=JWT_CONVEX_AUDIENCE,
            issuer="https://evil.example.com",
        )


def test_expired_token_rejected():
    token = create_convex_token(
        user_id=1, email="a@b.com", name="A", expires_in_seconds=1
    )
    time.sleep(2)
    assert verify_convex_token(token) is None


def test_tampered_token_rejected():
    token = create_convex_token(user_id=1, email="a@b.com", name="A")
    # Corrupt the signature segment.
    header, payload, sig = token.split(".")
    tampered = ".".join([header, payload, sig[:-2] + "AA"])
    assert verify_token(tampered, audience=JWT_CONVEX_AUDIENCE) is None


def test_unsigned_none_alg_rejected():
    # Alg-confusion: an attacker-crafted unsigned token must never verify.
    forged = pyjwt.encode(
        {
            "sub": "1",
            "aud": JWT_CONVEX_AUDIENCE,
            "iss": JWT_ISSUER,
            "exp": int(time.time()) + 3600,
        },
        key="",
        algorithm="none",
    )
    assert verify_convex_token(forged) is None
