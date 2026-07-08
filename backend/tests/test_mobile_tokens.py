"""
Tests for db/mobile.py — mobile refresh-token rotation with reuse detection,
and single-use auth codes / web session tickets.

Security-critical: token rotation must be one-shot, and presenting an
already-rotated (revoked but unexpired) refresh token is a reuse signal that
must revoke the whole device's token family.
"""
from datetime import timedelta

from config import Config
from db.mobile import (
    consume_mobile_auth_code,
    consume_mobile_web_ticket,
    insert_mobile_auth_code,
    insert_mobile_refresh_token,
    insert_mobile_web_ticket,
    rotate_mobile_refresh_token,
    revoke_mobile_refresh_token_for_user,
    utcnow,
)
from db.pool import get_conn

USER = 7
DEVICE = "device-abc"


def _insert_refresh(token_hash: str, now, *, user_id=USER, device_id=DEVICE, ttl_days=30):
    insert_mobile_refresh_token(
        user_id=user_id,
        token_hash=token_hash,
        device_id=device_id,
        issued_at=now,
        expires_at=now + timedelta(days=ttl_days),
    )


def _token_row(token_hash: str) -> dict:
    conn = get_conn(Config.MAIN_DB_PATH)
    row = conn.execute(
        "SELECT * FROM mobile_refresh_tokens WHERE token_hash = ?", (token_hash,)
    ).fetchone()
    return dict(row) if row else None


class TestRefreshRotation:
    def test_rotation_issues_new_token_and_revokes_old(self, main_db):
        now = utcnow()
        _insert_refresh("hash-a", now)
        status, info = rotate_mobile_refresh_token(
            "hash-a", "hash-b", DEVICE, now + timedelta(minutes=1), now + timedelta(days=30)
        )
        assert status == "ok"
        assert info == {"user_id": USER, "device_id": DEVICE}
        assert _token_row("hash-a")["revoked_at"] is not None
        new = _token_row("hash-b")
        assert new is not None and new["revoked_at"] is None
        assert new["user_id"] == USER and new["device_id"] == DEVICE

    def test_reuse_of_rotated_token_detected_and_revokes_device_family(self, main_db):
        now = utcnow()
        _insert_refresh("hash-a", now)
        assert rotate_mobile_refresh_token(
            "hash-a", "hash-b", DEVICE, now + timedelta(minutes=1), now + timedelta(days=30)
        )[0] == "ok"

        # Attacker replays the already-rotated token.
        status, info = rotate_mobile_refresh_token(
            "hash-a", "hash-c", DEVICE, now + timedelta(minutes=2), now + timedelta(days=30)
        )
        assert status == "reuse_detected"
        assert info["user_id"] == USER

        # The legitimate successor token must have been revoked too...
        assert _token_row("hash-b")["revoked_at"] is not None
        # ...and no replacement token was minted for the attacker.
        assert _token_row("hash-c") is None
        # The now-revoked successor can no longer be rotated normally.
        status, _ = rotate_mobile_refresh_token(
            "hash-b", "hash-d", DEVICE, now + timedelta(minutes=3), now + timedelta(days=30)
        )
        assert status != "ok"

    def test_expired_token_rejected(self, main_db):
        now = utcnow()
        _insert_refresh("hash-a", now, ttl_days=1)
        status, _ = rotate_mobile_refresh_token(
            "hash-a", "hash-b", DEVICE, now + timedelta(days=2), now + timedelta(days=32)
        )
        assert status == "invalid"
        assert _token_row("hash-a")["revoked_at"] is not None  # expired tokens get tombstoned
        assert _token_row("hash-b") is None

    def test_device_mismatch_rejected_without_rotation(self, main_db):
        now = utcnow()
        _insert_refresh("hash-a", now)
        status, _ = rotate_mobile_refresh_token(
            "hash-a", "hash-b", "other-device", now + timedelta(minutes=1), now + timedelta(days=30)
        )
        assert status == "device_mismatch"
        assert _token_row("hash-a")["revoked_at"] is None  # still valid for real device
        assert _token_row("hash-b") is None

    def test_unknown_token_invalid(self, main_db):
        status, info = rotate_mobile_refresh_token(
            "no-such-hash", "hash-b", DEVICE, utcnow(), utcnow() + timedelta(days=30)
        )
        assert status == "invalid"
        assert info is None

    def test_explicit_revocation_scoped_to_user(self, main_db):
        now = utcnow()
        _insert_refresh("hash-a", now)
        # A different user must not be able to revoke someone else's token.
        assert revoke_mobile_refresh_token_for_user("hash-a", USER + 1, now) == 0
        assert _token_row("hash-a")["revoked_at"] is None
        assert revoke_mobile_refresh_token_for_user("hash-a", USER, now) == 1
        assert _token_row("hash-a")["revoked_at"] is not None


class TestAuthCodes:
    def _insert_code(self, code_hash: str, now, ttl_seconds=120):
        insert_mobile_auth_code(
            code_hash=code_hash,
            user_id=USER,
            expires_at=now + timedelta(seconds=ttl_seconds),
            provider="google",
            redirect_uri="pinewoodone://auth/callback",
            state_nonce="nonce-1",
        )

    def test_code_is_single_use(self, main_db):
        now = utcnow()
        self._insert_code("code-a", now)
        status, row = consume_mobile_auth_code("code-a", now + timedelta(seconds=1))
        assert status == "ok"
        assert row["user_id"] == USER
        # Second presentation of the same code must be rejected.
        status, _ = consume_mobile_auth_code("code-a", now + timedelta(seconds=2))
        assert status == "consumed"

    def test_expired_code_rejected(self, main_db):
        now = utcnow()
        self._insert_code("code-a", now, ttl_seconds=120)
        status, _ = consume_mobile_auth_code("code-a", now + timedelta(seconds=121))
        assert status == "expired"

    def test_unknown_code_invalid(self, main_db):
        status, row = consume_mobile_auth_code("nope", utcnow())
        assert status == "invalid"
        assert row is None


class TestWebTickets:
    def test_ticket_is_single_use(self, main_db):
        now = utcnow()
        insert_mobile_web_ticket("ticket-a", USER, DEVICE, now + timedelta(seconds=60))
        status, row = consume_mobile_web_ticket("ticket-a", now + timedelta(seconds=1))
        assert status == "ok"
        assert row["user_id"] == USER and row["device_id"] == DEVICE
        status, _ = consume_mobile_web_ticket("ticket-a", now + timedelta(seconds=2))
        assert status == "consumed"

    def test_expired_ticket_rejected(self, main_db):
        now = utcnow()
        insert_mobile_web_ticket("ticket-a", USER, DEVICE, now + timedelta(seconds=60))
        status, _ = consume_mobile_web_ticket("ticket-a", now + timedelta(seconds=61))
        assert status == "expired"
