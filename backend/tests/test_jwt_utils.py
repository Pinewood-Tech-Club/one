"""
Tests for auth/jwt_utils.py — RS256 minting/verification for the Convex and
mobile audiences, plus the JWKS document.

Security-critical: covers expiry, signature tampering, payload tampering,
audience/issuer confusion, alg=none, and cross-key forgery.
"""
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from config import Config
from auth import jwt_utils
from auth.jwt_utils import (
    JWT_ALGORITHM,
    JWT_CONVEX_AUDIENCE,
    JWT_DEFAULT_CONVEX_EXPIRATION_HOURS,
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
from tests.conftest import TEST_PRIVATE_KEY_PEM, TEST_PUBLIC_KEY_PEM

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

    def test_header_declares_typ_jwt(self):
        # The `typ: "JWT"` header constant must be minted verbatim.
        token = create_token(**USER, audience=JWT_MOBILE_AUDIENCE)
        header = pyjwt.get_unverified_header(token)
        assert header["typ"] == "JWT"

    def test_module_algorithm_is_rs256(self):
        # A downgrade of the signing algorithm constant is a security regression.
        assert JWT_ALGORITHM == "RS256"

    def test_sub_is_stringified_user_id(self):
        # sub must be the *string* form of the id (str(user_id)), never the raw int.
        token = create_token(**USER, audience=JWT_MOBILE_AUDIENCE)
        claims = verify_token(token, audience=JWT_MOBILE_AUDIENCE)
        assert claims["sub"] == "42"
        assert isinstance(claims["sub"], str)

    def test_extra_claims_are_merged_into_payload(self):
        # The `if extra_claims: payload.update(...)` branch must actually run.
        token = create_token(
            **USER,
            audience=JWT_MOBILE_AUDIENCE,
            extra_claims={"role": "student", "scope": "read"},
        )
        claims = verify_token(token, audience=JWT_MOBILE_AUDIENCE)
        assert claims["role"] == "student"
        assert claims["scope"] == "read"

    def test_no_extra_claims_leaves_payload_clean(self):
        # Without extra_claims, create_token must not inject a jti/device_id.
        token = create_token(**USER, audience=JWT_MOBILE_AUDIENCE)
        claims = verify_token(token, audience=JWT_MOBILE_AUDIENCE)
        assert "jti" not in claims
        assert "device_id" not in claims

    def test_convex_default_expiry_is_24h(self):
        # Default Convex TTL must be exactly JWT_DEFAULT_CONVEX_EXPIRATION_HOURS hours.
        token = create_convex_token(**USER)
        claims = verify_convex_token(token)
        expected = JWT_DEFAULT_CONVEX_EXPIRATION_HOURS * 3600
        assert claims["exp"] - claims["iat"] == expected
        assert expected == 24 * 3600

    def test_mobile_default_expiry_matches_config_ttl(self):
        # Default mobile TTL must come from Config.MOBILE_ACCESS_TOKEN_TTL_SECONDS,
        # and it must differ from the Convex default so the audience branch matters.
        token = create_token(**USER, audience=JWT_MOBILE_AUDIENCE)
        claims = verify_token(token, audience=JWT_MOBILE_AUDIENCE)
        assert claims["exp"] - claims["iat"] == Config.MOBILE_ACCESS_TOKEN_TTL_SECONDS
        assert Config.MOBILE_ACCESS_TOKEN_TTL_SECONDS != JWT_DEFAULT_CONVEX_EXPIRATION_HOURS * 3600

    def test_audience_selects_default_expiry(self):
        # The `audience == JWT_CONVEX_AUDIENCE` guard must map each audience to its
        # own default TTL; flipping the comparison swaps these and fails here.
        convex = verify_convex_token(create_token(**USER, audience=JWT_CONVEX_AUDIENCE))
        mobile = verify_token(
            create_token(**USER, audience=JWT_MOBILE_AUDIENCE), audience=JWT_MOBILE_AUDIENCE
        )
        assert convex["exp"] - convex["iat"] == JWT_DEFAULT_CONVEX_EXPIRATION_HOURS * 3600
        assert mobile["exp"] - mobile["iat"] == Config.MOBILE_ACCESS_TOKEN_TTL_SECONDS

    def test_explicit_expiry_overrides_default(self):
        # An explicit expires_in_seconds must be honored verbatim (the None-guard branch).
        token = create_token(**USER, audience=JWT_CONVEX_AUDIENCE, expires_in_seconds=3600)
        claims = verify_convex_token(token)
        assert claims["exp"] - claims["iat"] == 3600

    def test_iat_is_current_time(self):
        # iat must be stamped at mint time (roughly now), not an arbitrary constant.
        before = int(datetime.now(timezone.utc).timestamp())
        token = create_token(**USER, audience=JWT_MOBILE_AUDIENCE)
        after = int(datetime.now(timezone.utc).timestamp())
        claims = verify_token(token, audience=JWT_MOBILE_AUDIENCE)
        assert before - 2 <= claims["iat"] <= after + 2

    def test_mobile_jti_is_present_and_full_length(self):
        # secrets.token_hex(16) -> 32 hex chars; verify presence and length.
        claims = verify_mobile_access_token(create_mobile_access_token(**USER, device_id="d1"))
        assert "jti" in claims
        assert len(claims["jti"]) == 32
        assert all(c in "0123456789abcdef" for c in claims["jti"])

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

    def test_hs256_confusion_with_public_key_rejected(self):
        # Classic RS/HS confusion: attacker HMAC-signs with the *public* key PEM as
        # the shared secret. Accepting HS256 (widening the algorithms list) would let
        # this forgery through, so verify_token must reject it. Built by hand because
        # pyjwt refuses to use a PEM as an HMAC secret at encode time.
        import hashlib
        import hmac

        now = datetime.now(timezone.utc)
        header = _b64url_encode(
            json.dumps({"alg": "HS256", "typ": "JWT", "kid": JWT_KEY_ID}).encode()
        )
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
        signing_input = f"{header}.{payload}".encode("ascii")
        sig = hmac.new(TEST_PUBLIC_KEY_PEM.encode("ascii"), signing_input, hashlib.sha256).digest()
        forged = f"{header}.{payload}.{_b64url_encode(sig)}"
        assert verify_token(forged, audience=JWT_MOBILE_AUDIENCE) is None

    def test_missing_audience_claim_rejected(self):
        # A token with no `aud` at all must not satisfy an audience-scoped verify.
        now = datetime.now(timezone.utc)
        token = pyjwt.encode(
            {"sub": "42", "iss": JWT_ISSUER, "iat": now, "exp": now + timedelta(minutes=5)},
            TEST_PRIVATE_KEY_PEM,
            algorithm="RS256",
        )
        assert verify_token(token, audience=JWT_MOBILE_AUDIENCE) is None

    def test_missing_issuer_claim_rejected(self):
        # A token with no `iss` must fail the issuer requirement.
        now = datetime.now(timezone.utc)
        token = pyjwt.encode(
            {"sub": "42", "aud": JWT_MOBILE_AUDIENCE, "iat": now, "exp": now + timedelta(minutes=5)},
            TEST_PRIVATE_KEY_PEM,
            algorithm="RS256",
        )
        assert verify_token(token, audience=JWT_MOBILE_AUDIENCE) is None

    def test_just_expired_token_rejected_at_boundary(self):
        # Expiry is exclusive: a token that expired one second ago is invalid.
        token = create_token(**USER, audience=JWT_MOBILE_AUDIENCE, expires_in_seconds=-1)
        assert verify_token(token, audience=JWT_MOBILE_AUDIENCE) is None

    def test_not_quite_expired_token_accepted(self):
        # Complement to the boundary test: a token valid for a few more seconds must
        # still verify, so the expiry check is a real boundary and not "always reject".
        token = create_token(**USER, audience=JWT_MOBILE_AUDIENCE, expires_in_seconds=30)
        assert verify_token(token, audience=JWT_MOBILE_AUDIENCE) is not None


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
        n_bytes = _b64url_decode(key["n"])
        e_bytes = _b64url_decode(key["e"])
        n = int.from_bytes(n_bytes, "big")
        e = int.from_bytes(e_bytes, "big")
        assert n == public_numbers.n
        assert e == public_numbers.e
        # RFC 7518: minimal big-endian octet encoding, no spurious leading zero byte.
        assert len(n_bytes) == (public_numbers.n.bit_length() + 7) // 8
        assert len(e_bytes) == (public_numbers.e.bit_length() + 7) // 8
        assert n_bytes[0] != 0
        assert e_bytes[0] != 0

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

    def test_jwks_exposes_standard_rsa_exponent(self):
        # e must round-trip to 65537 (the public_exponent the keys are generated with);
        # this also pins _int_to_base64url's byte-length math against off-by-one mutants.
        key = get_jwks()["keys"][0]
        e = int.from_bytes(_b64url_decode(key["e"]), "big")
        assert e == 65537

    def test_jwks_modulus_is_2048_bit(self):
        # n must be the full RSA-2048 modulus; a truncated/padded byte length would
        # change its bit length. Bounds allow for the top bit occasionally being 0.
        key = get_jwks()["keys"][0]
        n = int.from_bytes(_b64url_decode(key["n"]), "big")
        assert 2040 <= n.bit_length() <= 2048

    def test_jwks_base64url_has_no_padding(self):
        # JWKS members must be unpadded base64url per RFC 7515/7518.
        key = get_jwks()["keys"][0]
        assert "=" not in key["n"] and "=" not in key["e"]
        assert "+" not in key["n"] and "/" not in key["n"]


class TestIntToBase64Url:
    def test_roundtrips_arbitrary_integers(self):
        # Directly pin the encoder: decode must recover the exact integer.
        for value in (1, 255, 256, 65537, 2 ** 32 - 1, 2 ** 64, 123456789012345678901234567890):
            encoded = jwt_utils._int_to_base64url(value)
            raw = _b64url_decode(encoded)
            assert "=" not in encoded  # unpadded
            assert int.from_bytes(raw, "big") == value
            # Minimal encoding: exact byte length, no spurious leading zero byte.
            assert len(raw) == (value.bit_length() + 7) // 8
            assert raw[0] != 0


class TestEnvKeyPairGuard:
    """_load_env_key_pair must require both PEMs together or neither."""

    def test_returns_none_when_neither_env_key_set(self, monkeypatch):
        monkeypatch.setattr(jwt_utils, "JWT_PRIVATE_KEY_PEM", None)
        monkeypatch.setattr(jwt_utils, "JWT_PUBLIC_KEY_PEM", None)
        assert jwt_utils._load_env_key_pair() is None

    def test_raises_when_only_private_key_set(self, monkeypatch):
        monkeypatch.setattr(jwt_utils, "JWT_PRIVATE_KEY_PEM", "priv")
        monkeypatch.setattr(jwt_utils, "JWT_PUBLIC_KEY_PEM", None)
        with pytest.raises(RuntimeError):
            jwt_utils._load_env_key_pair()

    def test_raises_when_only_public_key_set(self, monkeypatch):
        monkeypatch.setattr(jwt_utils, "JWT_PRIVATE_KEY_PEM", None)
        monkeypatch.setattr(jwt_utils, "JWT_PUBLIC_KEY_PEM", "pub")
        with pytest.raises(RuntimeError):
            jwt_utils._load_env_key_pair()

    def test_returns_encoded_pair_when_both_set(self, monkeypatch):
        monkeypatch.setattr(jwt_utils, "JWT_PRIVATE_KEY_PEM", "priv-pem")
        monkeypatch.setattr(jwt_utils, "JWT_PUBLIC_KEY_PEM", "pub-pem")
        pair = jwt_utils._load_env_key_pair()
        assert pair == (b"priv-pem", b"pub-pem")
