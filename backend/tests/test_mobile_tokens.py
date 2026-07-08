"""
Tests for db/mobile.py — mobile refresh-token rotation with reuse detection,
device binding, single-use auth codes / web session tickets / Schoology OAuth
requests, and the notification-event outbox.

Security-critical: token rotation must be one-shot, and presenting an
already-rotated (revoked but unexpired) refresh token is a reuse signal that
must revoke the whole device's token family.

These tests are written as SPECIFICATION, not characterization: every guard
asserts that it actually protects the thing it guards (correct status AND the
security side effect), boundary comparisons are pinned at the exact instant,
and cross-user / cross-device scoping is asserted negatively so a dropped
WHERE clause is caught.
"""
from datetime import datetime, timedelta, timezone

from config import Config
from db.mobile import (
    consume_mobile_auth_code,
    consume_mobile_schoology_oauth_request,
    consume_mobile_web_ticket,
    fetch_pending_mobile_notification_events,
    insert_mobile_auth_code,
    insert_mobile_notification_event,
    insert_mobile_refresh_token,
    insert_mobile_schoology_oauth_request,
    insert_mobile_web_ticket,
    mark_mobile_notification_event_failed,
    mark_mobile_notification_event_processed,
    mark_mobile_notification_event_processing,
    parse_db_time,
    revoke_mobile_device,
    revoke_mobile_refresh_token_for_user,
    revoke_mobile_refresh_tokens_for_device,
    revoke_mobile_refresh_tokens_for_user,
    rotate_mobile_refresh_token,
    to_db_time,
    upsert_mobile_device,
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


def _row(table: str, col: str, val) -> dict:
    conn = get_conn(Config.MAIN_DB_PATH)
    row = conn.execute(
        f"SELECT * FROM {table} WHERE {col} = ?", (val,)
    ).fetchone()
    return dict(row) if row else None


class TestTimeHelpers:
    """to_db_time / parse_db_time must normalise everything to UTC. The whole
    module's expiry logic rests on this, so pin it directly."""

    def test_to_db_time_normalises_aware_to_utc(self):
        # A +05:00 instant must be serialised as its UTC equivalent, not left
        # in the original zone and not converted to the host's local zone.
        dt = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=5)))
        assert to_db_time(dt) == "2026-01-01T22:04:05+00:00"

    def test_to_db_time_utc_input_roundtrips(self):
        dt = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        assert to_db_time(dt) == "2026-01-02T03:04:05+00:00"

    def test_parse_db_time_none_and_empty(self):
        assert parse_db_time(None) is None
        assert parse_db_time("") is None

    def test_parse_db_time_naive_is_assumed_utc(self):
        # A stored value with no offset must be read back as that same wall
        # time in UTC, NOT reinterpreted through the host's local zone.
        r = parse_db_time("2026-06-01T12:00:00")
        assert r == datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert r.utcoffset() == timedelta(0)

    def test_parse_db_time_aware_converted_to_utc(self):
        r = parse_db_time("2026-01-02T03:04:05+05:00")
        assert r.utcoffset() == timedelta(0)
        assert r == datetime(2026, 1, 1, 22, 4, 5, tzinfo=timezone.utc)


class TestRefreshRotation:
    def test_rotation_issues_new_token_and_revokes_old(self, main_db):
        now = utcnow()
        _insert_refresh("hash-a", now)
        rotate_at = now + timedelta(minutes=1)
        new_exp = now + timedelta(days=30)
        status, info = rotate_mobile_refresh_token(
            "hash-a", "hash-b", DEVICE, rotate_at, new_exp
        )
        assert status == "ok"
        assert info == {"user_id": USER, "device_id": DEVICE}

        old = _token_row("hash-a")
        assert old["revoked_at"] is not None
        # The old token's revocation and last_used stamps are the rotation time.
        assert parse_db_time(old["revoked_at"]) == rotate_at
        assert parse_db_time(old["last_used_at"]) == rotate_at

        new = _token_row("hash-b")
        assert new is not None and new["revoked_at"] is None
        assert new["user_id"] == USER and new["device_id"] == DEVICE
        # The successor carries the requested expiry and is stamped at issue.
        assert parse_db_time(new["expires_at"]) == new_exp
        assert parse_db_time(new["issued_at"]) == rotate_at
        assert parse_db_time(new["last_used_at"]) == rotate_at

    def test_reuse_of_rotated_token_detected_and_revokes_device_family(self, main_db):
        now = utcnow()
        _insert_refresh("hash-a", now)
        assert rotate_mobile_refresh_token(
            "hash-a", "hash-b", DEVICE, now + timedelta(minutes=1), now + timedelta(days=30)
        )[0] == "ok"

        # Attacker replays the already-rotated token.
        reuse_at = now + timedelta(minutes=2)
        status, info = rotate_mobile_refresh_token(
            "hash-a", "hash-c", DEVICE, reuse_at, now + timedelta(days=30)
        )
        assert status == "reuse_detected"
        assert info["user_id"] == USER

        # The legitimate successor token must have been revoked too...
        successor = _token_row("hash-b")
        assert successor["revoked_at"] is not None
        assert parse_db_time(successor["revoked_at"]) == reuse_at
        # ...and no replacement token was minted for the attacker.
        assert _token_row("hash-c") is None
        # The now-revoked successor can no longer be rotated normally.
        status, _ = rotate_mobile_refresh_token(
            "hash-b", "hash-d", DEVICE, now + timedelta(minutes=3), now + timedelta(days=30)
        )
        assert status != "ok"

    def test_reuse_family_revocation_is_scoped_to_user_and_device(self, main_db):
        """The reuse response must revoke ONLY the compromised device's family,
        never another device of the same user or the same device of another
        user. This pins both predicates of the family-revocation WHERE clause."""
        now = utcnow()
        _insert_refresh("hash-a", now)  # USER / DEVICE, will be rotated
        assert rotate_mobile_refresh_token(
            "hash-a", "hash-b", DEVICE, now + timedelta(minutes=1), now + timedelta(days=30)
        )[0] == "ok"

        # Bystander tokens that MUST survive the reuse blast radius.
        _insert_refresh("hash-same-user-other-device", now, device_id="device-2")
        _insert_refresh("hash-other-user-same-device", now, user_id=USER + 1)

        status, _ = rotate_mobile_refresh_token(
            "hash-a", "hash-c", DEVICE, now + timedelta(minutes=2), now + timedelta(days=30)
        )
        assert status == "reuse_detected"

        # In-family successor revoked; out-of-family tokens untouched.
        assert _token_row("hash-b")["revoked_at"] is not None
        assert _token_row("hash-same-user-other-device")["revoked_at"] is None
        assert _token_row("hash-other-user-same-device")["revoked_at"] is None

    def test_reuse_of_revoked_and_expired_token_is_invalid_not_reuse(self, main_db):
        """A revoked token that is ALSO past expiry is stale, not a live reuse
        signal: it must return 'invalid' and must NOT nuke the device family."""
        now = utcnow()
        # Insert a token, rotate it (revokes it), leaving an unexpired successor.
        _insert_refresh("hash-a", now, ttl_days=1)
        assert rotate_mobile_refresh_token(
            "hash-a", "hash-b", DEVICE, now + timedelta(minutes=1), now + timedelta(days=30)
        )[0] == "ok"

        # Present hash-a again well after its own expiry (issued now, ttl 1d).
        status, _ = rotate_mobile_refresh_token(
            "hash-a", "hash-x", DEVICE, now + timedelta(days=2), now + timedelta(days=30)
        )
        assert status == "invalid"
        # The successor family must remain intact (no reuse revocation fired).
        assert _token_row("hash-b")["revoked_at"] is None
        assert _token_row("hash-x") is None

    def test_reuse_boundary_expiry_equal_now_is_invalid(self, main_db):
        """At the exact expiry instant a revoked token is expired => invalid,
        not reuse (pins the strict '>' in the reuse expiry check)."""
        now = utcnow()
        _insert_refresh("hash-a", now, ttl_days=1)
        expiry = now + timedelta(days=1)  # == stored expires_at for hash-a
        # First rotate to revoke hash-a (still valid one second before expiry).
        assert rotate_mobile_refresh_token(
            "hash-a", "hash-b", DEVICE, now + timedelta(minutes=1), now + timedelta(days=30)
        )[0] == "ok"
        # Replay hash-a exactly at its expiry instant.
        status, _ = rotate_mobile_refresh_token(
            "hash-a", "hash-y", DEVICE, expiry, now + timedelta(days=30)
        )
        assert status == "invalid"
        assert _token_row("hash-b")["revoked_at"] is None  # family untouched

    def test_expired_token_rejected(self, main_db):
        now = utcnow()
        _insert_refresh("hash-a", now, ttl_days=1)
        status, _ = rotate_mobile_refresh_token(
            "hash-a", "hash-b", DEVICE, now + timedelta(days=2), now + timedelta(days=32)
        )
        assert status == "invalid"
        assert _token_row("hash-a")["revoked_at"] is not None  # expired tokens get tombstoned
        assert _token_row("hash-b") is None

    def test_expiry_boundary_equal_now_is_rejected(self, main_db):
        """A token whose expiry equals 'now' must be treated as expired
        (pins the '<=' boundary in the active-token expiry check)."""
        now = utcnow()
        _insert_refresh("hash-a", now, ttl_days=1)
        expiry = now + timedelta(days=1)  # == stored expires_at
        status, _ = rotate_mobile_refresh_token(
            "hash-a", "hash-b", DEVICE, expiry, expiry + timedelta(days=30)
        )
        assert status == "invalid"
        assert _token_row("hash-a")["revoked_at"] is not None  # tombstoned
        assert _token_row("hash-b") is None  # no successor minted

    def test_one_second_before_expiry_still_rotates(self, main_db):
        """Complement to the boundary test: strictly before expiry must succeed,
        so the boundary test above can't pass by rejecting everything."""
        now = utcnow()
        _insert_refresh("hash-a", now, ttl_days=1)
        just_before = now + timedelta(days=1) - timedelta(seconds=1)
        status, _ = rotate_mobile_refresh_token(
            "hash-a", "hash-b", DEVICE, just_before, now + timedelta(days=31)
        )
        assert status == "ok"
        assert _token_row("hash-b") is not None

    def test_device_mismatch_rejected_without_rotation(self, main_db):
        now = utcnow()
        _insert_refresh("hash-a", now)
        status, _ = rotate_mobile_refresh_token(
            "hash-a", "hash-b", "other-device", now + timedelta(minutes=1), now + timedelta(days=30)
        )
        assert status == "device_mismatch"
        assert _token_row("hash-a")["revoked_at"] is None  # still valid for real device
        assert _token_row("hash-a")["last_used_at"] is not None
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

    def test_explicit_revocation_is_idempotent_preserving_first_stamp(self, main_db):
        """COALESCE means a second revoke keeps the original revoked_at while
        still touching last_used_at. This pins the COALESCE against a naive
        'revoked_at = ?' overwrite."""
        now = utcnow()
        _insert_refresh("hash-a", now)
        first = now + timedelta(minutes=1)
        assert revoke_mobile_refresh_token_for_user("hash-a", USER, first) == 1
        row1 = _token_row("hash-a")
        assert parse_db_time(row1["revoked_at"]) == first

        second = now + timedelta(minutes=5)
        assert revoke_mobile_refresh_token_for_user("hash-a", USER, second) == 1
        row2 = _token_row("hash-a")
        # revoked_at frozen at the first revocation...
        assert parse_db_time(row2["revoked_at"]) == first
        # ...but last_used_at advanced to the latest touch.
        assert parse_db_time(row2["last_used_at"]) == second


class TestBulkRevocation:
    def test_revoke_all_for_user_hits_every_device_but_not_other_users(self, main_db):
        now = utcnow()
        _insert_refresh("u7-d1", now, device_id="d1")
        _insert_refresh("u7-d2", now, device_id="d2")
        _insert_refresh("u8-d1", now, user_id=USER + 1, device_id="d1")

        revoke_mobile_refresh_tokens_for_user(USER, now)

        assert _token_row("u7-d1")["revoked_at"] is not None
        assert _token_row("u7-d2")["revoked_at"] is not None
        # Another user's token must be untouched.
        assert _token_row("u8-d1")["revoked_at"] is None

    def test_revoke_all_for_user_preserves_earlier_revocation(self, main_db):
        now = utcnow()
        _insert_refresh("u7-d1", now, device_id="d1")
        early = now - timedelta(hours=1)
        revoke_mobile_refresh_token_for_user("u7-d1", USER, early)
        # Bulk revoke later must not overwrite the earlier revoked_at (COALESCE).
        revoke_mobile_refresh_tokens_for_user(USER, now)
        assert parse_db_time(_token_row("u7-d1")["revoked_at"]) == early

    def test_revoke_for_device_scoped_to_user_and_device(self, main_db):
        now = utcnow()
        _insert_refresh("u7-d1", now, device_id="d1")
        _insert_refresh("u7-d2", now, device_id="d2")
        _insert_refresh("u8-d1", now, user_id=USER + 1, device_id="d1")

        revoke_mobile_refresh_tokens_for_device(USER, "d1", now)

        assert _token_row("u7-d1")["revoked_at"] is not None
        # Same user, different device: untouched.
        assert _token_row("u7-d2")["revoked_at"] is None
        # Different user, same device id: untouched.
        assert _token_row("u8-d1")["revoked_at"] is None

    def test_revoke_for_device_preserves_earlier_revocation(self, main_db):
        now = utcnow()
        _insert_refresh("u7-d1", now, device_id="d1")
        early = now - timedelta(hours=2)
        revoke_mobile_refresh_token_for_user("u7-d1", USER, early)
        revoke_mobile_refresh_tokens_for_device(USER, "d1", now)
        assert parse_db_time(_token_row("u7-d1")["revoked_at"]) == early


class TestDeviceRegistration:
    def _fields(self, **over):
        base = dict(
            user_id=USER,
            device_id=DEVICE,
            platform="ios",
            app_version="1.0.0",
            push_token="ptok",
            push_env="prod",
            locale="en-US",
            timezone_value="America/Los_Angeles",
        )
        base.update(over)
        return base

    def test_insert_stores_all_fields(self, main_db):
        now = utcnow()
        upsert_mobile_device(now=now, **self._fields())
        row = _row("mobile_devices", "device_id", DEVICE)
        assert row["user_id"] == USER
        assert row["platform"] == "ios"
        assert row["app_version"] == "1.0.0"
        assert row["push_token"] == "ptok"
        assert row["push_env"] == "prod"
        assert row["locale"] == "en-US"
        assert row["timezone"] == "America/Los_Angeles"
        assert row["revoked_at"] is None
        assert parse_db_time(row["created_at"]) == now
        assert parse_db_time(row["updated_at"]) == now
        assert parse_db_time(row["last_seen_at"]) == now

    def test_upsert_updates_mutable_fields_and_preserves_created_at(self, main_db):
        t0 = utcnow()
        upsert_mobile_device(now=t0, **self._fields())
        t1 = t0 + timedelta(hours=3)
        upsert_mobile_device(
            now=t1,
            **self._fields(
                platform="android",
                app_version="2.0.0",
                push_token="ptok2",
                push_env="dev",
                locale="fr-FR",
                timezone_value="Europe/Paris",
            ),
        )
        row = _row("mobile_devices", "device_id", DEVICE)
        assert row["platform"] == "android"
        assert row["app_version"] == "2.0.0"
        assert row["push_token"] == "ptok2"
        assert row["push_env"] == "dev"
        assert row["locale"] == "fr-FR"
        assert row["timezone"] == "Europe/Paris"
        # created_at is immutable; updated_at / last_seen_at advance.
        assert parse_db_time(row["created_at"]) == t0
        assert parse_db_time(row["updated_at"]) == t1
        assert parse_db_time(row["last_seen_at"]) == t1
        # Exactly one row for this (user, device) — the upsert did not duplicate.
        conn = get_conn(Config.MAIN_DB_PATH)
        cnt = conn.execute(
            "SELECT COUNT(*) FROM mobile_devices WHERE user_id = ? AND device_id = ?",
            (USER, DEVICE),
        ).fetchone()[0]
        assert cnt == 1

    def test_reregistration_clears_revocation(self, main_db):
        """Re-registering a revoked device must un-revoke it (revoked_at reset
        to NULL). This is the ON CONFLICT ... revoked_at = NULL behaviour."""
        t0 = utcnow()
        upsert_mobile_device(now=t0, **self._fields())
        assert revoke_mobile_device(USER, DEVICE, t0 + timedelta(minutes=1)) == 1
        assert _row("mobile_devices", "device_id", DEVICE)["revoked_at"] is not None

        upsert_mobile_device(now=t0 + timedelta(minutes=2), **self._fields())
        assert _row("mobile_devices", "device_id", DEVICE)["revoked_at"] is None

    def test_revoke_device_scoped_and_idempotent(self, main_db):
        t0 = utcnow()
        upsert_mobile_device(now=t0, **self._fields())
        upsert_mobile_device(now=t0, **self._fields(device_id="other-dev"))

        # Wrong user cannot revoke.
        assert revoke_mobile_device(USER + 1, DEVICE, t0) == 0
        assert _row("mobile_devices", "device_id", DEVICE)["revoked_at"] is None

        first = t0 + timedelta(minutes=1)
        assert revoke_mobile_device(USER, DEVICE, first) == 1
        row = _row("mobile_devices", "device_id", DEVICE)
        assert parse_db_time(row["revoked_at"]) == first
        # The other device of the same user is untouched.
        assert _row("mobile_devices", "device_id", "other-dev")["revoked_at"] is None

        # Idempotent: second revoke keeps the original stamp (COALESCE).
        assert revoke_mobile_device(USER, DEVICE, t0 + timedelta(minutes=5)) == 1
        assert parse_db_time(_row("mobile_devices", "device_id", DEVICE)["revoked_at"]) == first


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

    def test_insert_stores_fields(self, main_db):
        now = utcnow()
        self._insert_code("code-a", now)
        row = _row("mobile_auth_codes", "code_hash", "code-a")
        assert row["user_id"] == USER
        assert row["provider"] == "google"
        assert row["redirect_uri"] == "pinewoodone://auth/callback"
        assert row["state_nonce"] == "nonce-1"
        assert row["consumed_at"] is None

    def test_code_is_single_use(self, main_db):
        now = utcnow()
        self._insert_code("code-a", now)
        consume_at = now + timedelta(seconds=1)
        status, row = consume_mobile_auth_code("code-a", consume_at)
        assert status == "ok"
        assert row["user_id"] == USER
        # The success path must actually stamp consumed_at in the DB.
        assert parse_db_time(_row("mobile_auth_codes", "code_hash", "code-a")["consumed_at"]) == consume_at
        # Second presentation of the same code must be rejected.
        status, _ = consume_mobile_auth_code("code-a", now + timedelta(seconds=2))
        assert status == "consumed"

    def test_expired_code_rejected(self, main_db):
        now = utcnow()
        self._insert_code("code-a", now, ttl_seconds=120)
        status, _ = consume_mobile_auth_code("code-a", now + timedelta(seconds=121))
        assert status == "expired"
        # Expiry must NOT consume the code (no consumed_at side effect).
        assert _row("mobile_auth_codes", "code_hash", "code-a")["consumed_at"] is None

    def test_expiry_boundary_equal_now_is_expired(self, main_db):
        now = utcnow()
        self._insert_code("code-a", now, ttl_seconds=120)
        exact = now + timedelta(seconds=120)  # == stored expires_at
        status, _ = consume_mobile_auth_code("code-a", exact)
        assert status == "expired"

    def test_one_second_before_expiry_consumes(self, main_db):
        now = utcnow()
        self._insert_code("code-a", now, ttl_seconds=120)
        status, _ = consume_mobile_auth_code("code-a", now + timedelta(seconds=119))
        assert status == "ok"

    def test_unknown_code_invalid(self, main_db):
        status, row = consume_mobile_auth_code("nope", utcnow())
        assert status == "invalid"
        assert row is None


class TestWebTickets:
    def test_ticket_is_single_use(self, main_db):
        now = utcnow()
        insert_mobile_web_ticket("ticket-a", USER, DEVICE, now + timedelta(seconds=60))
        consume_at = now + timedelta(seconds=1)
        status, row = consume_mobile_web_ticket("ticket-a", consume_at)
        assert status == "ok"
        assert row["user_id"] == USER and row["device_id"] == DEVICE
        # Success path stamps consumed_at.
        assert parse_db_time(
            _row("mobile_web_session_tickets", "ticket_hash", "ticket-a")["consumed_at"]
        ) == consume_at
        status, _ = consume_mobile_web_ticket("ticket-a", now + timedelta(seconds=2))
        assert status == "consumed"

    def test_expired_ticket_rejected(self, main_db):
        now = utcnow()
        insert_mobile_web_ticket("ticket-a", USER, DEVICE, now + timedelta(seconds=60))
        status, _ = consume_mobile_web_ticket("ticket-a", now + timedelta(seconds=61))
        assert status == "expired"
        assert _row("mobile_web_session_tickets", "ticket_hash", "ticket-a")["consumed_at"] is None

    def test_expiry_boundary_equal_now_is_expired(self, main_db):
        now = utcnow()
        insert_mobile_web_ticket("ticket-a", USER, DEVICE, now + timedelta(seconds=60))
        status, _ = consume_mobile_web_ticket("ticket-a", now + timedelta(seconds=60))
        assert status == "expired"

    def test_unknown_ticket_invalid(self, main_db):
        status, row = consume_mobile_web_ticket("no-such-ticket", utcnow())
        assert status == "invalid"
        assert row is None


class TestSchoologyOAuthRequests:
    def _insert(self, req_hash, now, ttl_seconds=300):
        insert_mobile_schoology_oauth_request(
            user_id=USER,
            request_token_hash=req_hash,
            request_token_secret_encrypted="enc-secret",
            device_id=DEVICE,
            redirect_uri="pinewoodone://schoology/callback",
            code_challenge="challenge-xyz",
            code_challenge_method="S256",
            client_state="cli-state",
            expires_at=now + timedelta(seconds=ttl_seconds),
            created_at=now,
        )

    def test_insert_stores_fields(self, main_db):
        now = utcnow()
        self._insert("req-a", now)
        row = _row("mobile_schoology_oauth_requests", "request_token_hash", "req-a")
        assert row["user_id"] == USER
        assert row["request_token_secret_encrypted"] == "enc-secret"
        assert row["device_id"] == DEVICE
        assert row["redirect_uri"] == "pinewoodone://schoology/callback"
        assert row["code_challenge"] == "challenge-xyz"
        assert row["code_challenge_method"] == "S256"
        assert row["client_state"] == "cli-state"
        assert row["consumed_at"] is None

    def test_request_is_single_use(self, main_db):
        now = utcnow()
        self._insert("req-a", now)
        consume_at = now + timedelta(seconds=1)
        status, row = consume_mobile_schoology_oauth_request("req-a", consume_at)
        assert status == "ok"
        assert row["request_token_secret_encrypted"] == "enc-secret"
        assert row["device_id"] == DEVICE
        assert row["code_challenge"] == "challenge-xyz"
        assert parse_db_time(
            _row("mobile_schoology_oauth_requests", "request_token_hash", "req-a")["consumed_at"]
        ) == consume_at
        status, _ = consume_mobile_schoology_oauth_request("req-a", now + timedelta(seconds=2))
        assert status == "consumed"

    def test_expired_request_rejected(self, main_db):
        now = utcnow()
        self._insert("req-a", now, ttl_seconds=300)
        status, _ = consume_mobile_schoology_oauth_request("req-a", now + timedelta(seconds=301))
        assert status == "expired"
        assert _row(
            "mobile_schoology_oauth_requests", "request_token_hash", "req-a"
        )["consumed_at"] is None

    def test_expiry_boundary_equal_now_is_expired(self, main_db):
        now = utcnow()
        self._insert("req-a", now, ttl_seconds=300)
        status, _ = consume_mobile_schoology_oauth_request("req-a", now + timedelta(seconds=300))
        assert status == "expired"

    def test_unknown_request_invalid(self, main_db):
        status, row = consume_mobile_schoology_oauth_request("nope", utcnow())
        assert status == "invalid"
        assert row is None


class TestNotificationEvents:
    def _insert(self, now, *, event_type="grade_posted", payload=None, status="pending",
                available_delta=timedelta(0), device_id=DEVICE, user_id=USER):
        return insert_mobile_notification_event(
            user_id=user_id,
            device_id=device_id,
            event_type=event_type,
            payload={"b": 2, "a": 1} if payload is None else payload,
            status=status,
            created_at=now,
            available_at=now + available_delta,
        )

    def test_insert_returns_id_and_stores_sorted_payload(self, main_db):
        now = utcnow()
        event_id = self._insert(now, payload={"b": 2, "a": 1})
        assert isinstance(event_id, int) and event_id > 0
        row = _row("mobile_notification_events", "id", event_id)
        assert row["user_id"] == USER
        assert row["device_id"] == DEVICE
        assert row["event_type"] == "grade_posted"
        assert row["status"] == "pending"
        # Payload serialised compact + key-sorted, deterministically.
        assert row["payload_json"] == '{"a":1,"b":2}'

    def test_fetch_pending_only_returns_due_pending_events(self, main_db):
        now = utcnow()
        due = self._insert(now, payload={"k": "due"})
        # Not yet available: available_at strictly in the future.
        self._insert(now, payload={"k": "future"}, available_delta=timedelta(hours=1))
        # Already processed: not pending.
        processed = self._insert(now, payload={"k": "done"})
        mark_mobile_notification_event_processing(processed, now)
        mark_mobile_notification_event_processed(processed, now)

        events = fetch_pending_mobile_notification_events(now)
        ids = {e["id"] for e in events}
        assert due in ids
        assert processed not in ids
        assert all(e["available_at"] is not None for e in events)
        # The future-dated event must be excluded at 'now'.
        assert len(ids) == 1
        # Payload is decoded back into a dict, payload_json key removed.
        due_event = next(e for e in events if e["id"] == due)
        assert due_event["payload"] == {"k": "due"}
        assert "payload_json" not in due_event

    def test_fetch_pending_becomes_available_at_boundary(self, main_db):
        now = utcnow()
        ev = self._insert(now, available_delta=timedelta(minutes=10))
        # Before availability: excluded.
        assert ev not in {e["id"] for e in fetch_pending_mobile_notification_events(now)}
        # At exactly available_at: included (available_at <= now).
        at = now + timedelta(minutes=10)
        assert ev in {e["id"] for e in fetch_pending_mobile_notification_events(at)}

    def test_fetch_pending_respects_limit_and_ordering(self, main_db):
        now = utcnow()
        first = self._insert(now, available_delta=timedelta(seconds=1))
        second = self._insert(now, available_delta=timedelta(seconds=2))
        third = self._insert(now, available_delta=timedelta(seconds=3))
        later = now + timedelta(minutes=1)
        events = fetch_pending_mobile_notification_events(later, limit=2)
        assert [e["id"] for e in events] == [first, second]
        assert third not in {e["id"] for e in events}

    def test_fetch_pending_limit_one_returns_single_event(self, main_db):
        """limit=1 must return exactly one row (pins max(1, limit) floor)."""
        now = utcnow()
        first = self._insert(now, available_delta=timedelta(seconds=1))
        self._insert(now, available_delta=timedelta(seconds=2))
        later = now + timedelta(minutes=1)
        events = fetch_pending_mobile_notification_events(later, limit=1)
        assert [e["id"] for e in events] == [first]

    def test_fetch_pending_falls_back_to_empty_dict_on_corrupt_payload(self, main_db):
        """A row whose payload_json is not valid JSON must decode to an empty
        dict under the 'payload' key, never None and never a renamed key."""
        now = utcnow()
        conn = get_conn(Config.MAIN_DB_PATH)
        conn.execute(
            """
            INSERT INTO mobile_notification_events
            (user_id, device_id, event_type, payload_json, status, created_at, available_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (USER, DEVICE, "grade_posted", "{not-valid-json", to_db_time(now), to_db_time(now)),
        )
        conn.commit()
        events = fetch_pending_mobile_notification_events(now + timedelta(seconds=1))
        assert len(events) == 1
        entry = events[0]
        assert "payload" in entry
        assert entry["payload"] == {}
        assert entry["payload"] is not None

    def test_mark_processing_only_affects_pending(self, main_db):
        now = utcnow()
        ev = self._insert(now)
        assert mark_mobile_notification_event_processing(ev, now) == 1
        assert _row("mobile_notification_events", "id", ev)["status"] == "processing"
        # Marking an already-processing event again is a no-op (guard on pending).
        assert mark_mobile_notification_event_processing(ev, now) == 0
        assert _row("mobile_notification_events", "id", ev)["status"] == "processing"

    def test_mark_processed_sets_status_and_timestamp(self, main_db):
        now = utcnow()
        ev = self._insert(now)
        mark_mobile_notification_event_processing(ev, now)
        done_at = now + timedelta(seconds=5)
        assert mark_mobile_notification_event_processed(ev, done_at) == 1
        row = _row("mobile_notification_events", "id", ev)
        assert row["status"] == "processed"
        assert parse_db_time(row["processed_at"]) == done_at
        assert row["last_error"] is None

    def test_mark_failed_records_error_truncated(self, main_db):
        now = utcnow()
        ev = self._insert(now)
        mark_mobile_notification_event_processing(ev, now)
        long_error = "x" * 5000
        failed_at = now + timedelta(seconds=9)
        assert mark_mobile_notification_event_failed(ev, failed_at, long_error) == 1
        row = _row("mobile_notification_events", "id", ev)
        assert row["status"] == "failed"
        assert parse_db_time(row["processed_at"]) == failed_at
        # Error is persisted but capped at 1000 chars.
        assert row["last_error"] == "x" * 1000
