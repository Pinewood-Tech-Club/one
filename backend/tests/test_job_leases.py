"""
Tests for db/job_leases.py — the per-user Schoology refresh lease that
deduplicates refresh work across processes.

Covers: fresh acquire, contention while held, expiry-based steal, and the
owner_token guard on release.
"""
from datetime import datetime, timedelta, timezone

from config import Config
from db.job_leases import (
    acquire_schoology_refresh_lease,
    parse_db_time,
    release_schoology_refresh_lease,
    to_db_time,
    utcnow,
)
from db.pool import get_conn

TTL = 300
USER = 1


def _lease_row(user_id):
    """Read the raw lease row straight from the DB (owner_token, expiry)."""
    conn = get_conn(Config.MAIN_DB_PATH)
    return conn.execute(
        "SELECT owner_token, started_at, lease_expires_at "
        "FROM schoology_refresh_leases WHERE user_id = ?",
        (user_id,),
    ).fetchone()


class TestAcquire:
    def test_fresh_acquire_succeeds(self, main_db):
        assert acquire_schoology_refresh_lease(USER, "owner-a", utcnow(), TTL) is True

    def test_second_acquire_blocked_while_lease_valid(self, main_db):
        now = utcnow()
        assert acquire_schoology_refresh_lease(USER, "owner-a", now, TTL) is True
        assert acquire_schoology_refresh_lease(USER, "owner-b", now + timedelta(seconds=1), TTL) is False

    def test_reacquire_by_same_owner_also_blocked_while_valid(self, main_db):
        # The lease is not reentrant: holding it does not allow re-acquiring.
        now = utcnow()
        assert acquire_schoology_refresh_lease(USER, "owner-a", now, TTL) is True
        assert acquire_schoology_refresh_lease(USER, "owner-a", now + timedelta(seconds=1), TTL) is False

    def test_leases_are_per_user(self, main_db):
        now = utcnow()
        assert acquire_schoology_refresh_lease(1, "owner-a", now, TTL) is True
        assert acquire_schoology_refresh_lease(2, "owner-b", now, TTL) is True


class TestExpiryAndSteal:
    def test_acquire_exactly_at_expiry_succeeds(self, main_db):
        # The guard is strict `expires_at > now`: at the instant the lease
        # expires (now == expires_at) it is NO LONGER held, so a competing
        # acquire must succeed. This pins the boundary and kills a `>` -> `>=`
        # mutation (which would keep the lease held for one extra instant).
        now = utcnow()
        acquire_schoology_refresh_lease(USER, "owner-a", now, TTL)
        exactly_at_expiry = now + timedelta(seconds=TTL)
        assert acquire_schoology_refresh_lease(USER, "owner-b", exactly_at_expiry, TTL) is True

    def test_acquire_one_second_before_expiry_blocked(self, main_db):
        # Complement of the boundary above: one second before expiry the lease
        # is still held, so a competing acquire must be refused. This pins the
        # expiry to exactly now + TTL, killing off-by-one / wrong-TTL mutations.
        now = utcnow()
        acquire_schoology_refresh_lease(USER, "owner-a", now, TTL)
        just_before_expiry = now + timedelta(seconds=TTL - 1)
        assert acquire_schoology_refresh_lease(USER, "owner-b", just_before_expiry, TTL) is False

    def test_acquire_persists_owner_and_expiry(self, main_db):
        # Specification of the write side effect: a successful acquire must
        # store THIS owner's token and a lease that expires exactly TTL later.
        now = utcnow()
        assert acquire_schoology_refresh_lease(USER, "owner-a", now, TTL) is True
        row = _lease_row(USER)
        assert row is not None
        assert row["owner_token"] == "owner-a"
        assert parse_db_time(row["lease_expires_at"]) == now + timedelta(seconds=TTL)
        assert parse_db_time(row["started_at"]) == now

    def test_steal_overwrites_owner_and_resets_expiry(self, main_db):
        # A steal must hand ownership to the new owner and re-stamp the expiry;
        # it must not leave the previous owner's token in place.
        now = utcnow()
        acquire_schoology_refresh_lease(USER, "owner-a", now, TTL)
        steal_time = now + timedelta(seconds=TTL + 1)
        assert acquire_schoology_refresh_lease(USER, "owner-b", steal_time, TTL) is True
        row = _lease_row(USER)
        assert row["owner_token"] == "owner-b"
        assert parse_db_time(row["lease_expires_at"]) == steal_time + timedelta(seconds=TTL)

    def test_expired_lease_can_be_stolen(self, main_db):
        now = utcnow()
        assert acquire_schoology_refresh_lease(USER, "owner-a", now, TTL) is True
        after_expiry = now + timedelta(seconds=TTL + 1)
        assert acquire_schoology_refresh_lease(USER, "owner-b", after_expiry, TTL) is True

    def test_steal_resets_ttl_for_new_owner(self, main_db):
        now = utcnow()
        acquire_schoology_refresh_lease(USER, "owner-a", now, TTL)
        steal_time = now + timedelta(seconds=TTL + 1)
        acquire_schoology_refresh_lease(USER, "owner-b", steal_time, TTL)
        # The stolen lease belongs to owner-b now: still held mid-TTL...
        assert acquire_schoology_refresh_lease(USER, "owner-c", steal_time + timedelta(seconds=TTL - 1), TTL) is False
        # ...and free again after owner-b's TTL elapses.
        assert acquire_schoology_refresh_lease(USER, "owner-c", steal_time + timedelta(seconds=TTL + 1), TTL) is True


class TestRelease:
    def test_release_by_owner_frees_lease(self, main_db):
        now = utcnow()
        acquire_schoology_refresh_lease(USER, "owner-a", now, TTL)
        release_schoology_refresh_lease(USER, "owner-a")
        assert acquire_schoology_refresh_lease(USER, "owner-b", now + timedelta(seconds=1), TTL) is True

    def test_release_by_owner_deletes_the_row(self, main_db):
        # Direct side-effect assertion: releasing as the owner removes the row.
        now = utcnow()
        acquire_schoology_refresh_lease(USER, "owner-a", now, TTL)
        assert _lease_row(USER) is not None
        release_schoology_refresh_lease(USER, "owner-a")
        assert _lease_row(USER) is None

    def test_release_by_non_owner_is_a_noop(self, main_db):
        # The owner_token guard: a stale/foreign owner must not release
        # someone else's active lease. If the `AND owner_token = ?` clause were
        # dropped from the DELETE, the row would vanish and owner-c could
        # acquire — so this asserts BOTH the row survives (with owner-a intact)
        # and that a fresh acquire is still refused.
        now = utcnow()
        acquire_schoology_refresh_lease(USER, "owner-a", now, TTL)
        release_schoology_refresh_lease(USER, "owner-b")
        row = _lease_row(USER)
        assert row is not None
        assert row["owner_token"] == "owner-a"
        assert acquire_schoology_refresh_lease(USER, "owner-c", now + timedelta(seconds=1), TTL) is False

    def test_release_is_scoped_to_the_target_user(self, main_db):
        # The user_id predicate in the DELETE must scope the release: releasing
        # user 1 must not free user 2's lease.
        now = utcnow()
        acquire_schoology_refresh_lease(1, "owner-a", now, TTL)
        acquire_schoology_refresh_lease(2, "owner-b", now, TTL)
        release_schoology_refresh_lease(1, "owner-a")
        assert _lease_row(1) is None
        assert _lease_row(2) is not None
        assert acquire_schoology_refresh_lease(2, "owner-c", now + timedelta(seconds=1), TTL) is False

    def test_original_owner_cannot_release_after_steal(self, main_db):
        # After expiry + steal, the original owner's token no longer matches:
        # its release must not free the new owner's lease.
        now = utcnow()
        acquire_schoology_refresh_lease(USER, "owner-a", now, TTL)
        steal_time = now + timedelta(seconds=TTL + 1)
        assert acquire_schoology_refresh_lease(USER, "owner-b", steal_time, TTL) is True
        release_schoology_refresh_lease(USER, "owner-a")
        assert acquire_schoology_refresh_lease(USER, "owner-c", steal_time + timedelta(seconds=1), TTL) is False


class TestTimeSerialization:
    """Direct specification of the DB time (de)serialization helpers.

    These matter because acquire's expiry comparison hinges on them: every
    stored/parsed timestamp must be normalized to UTC. A helper that quietly
    dropped UTC normalization would let leases mis-expire.
    """

    def test_to_db_time_normalizes_to_utc(self):
        # An input in a non-UTC zone must be serialized as the equivalent UTC
        # instant with a UTC offset — not re-expressed in local/None tz.
        aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
        result = to_db_time(aware)
        assert result == "2026-01-01T07:00:00+00:00"
        # Round-trips back to the same instant tagged UTC.
        assert parse_db_time(result) == datetime(2026, 1, 1, 7, 0, 0, tzinfo=timezone.utc)

    def test_parse_db_time_returns_none_for_empty(self):
        # The `if not value` guard: missing/empty timestamps yield None rather
        # than raising. (No row -> no expiry -> lease is free.)
        assert parse_db_time(None) is None
        assert parse_db_time("") is None

    def test_parse_db_time_treats_naive_string_as_utc(self):
        # A stored value without an offset must be interpreted as UTC, not as
        # the machine's local time. Asserting both the instant and a zero
        # offset kills mutations that skip the tzinfo assignment or drop the
        # final UTC conversion.
        parsed = parse_db_time("2026-01-01T00:00:00")
        assert parsed == datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert parsed.utcoffset() == timedelta(0)

    def test_parse_db_time_converts_aware_offset_to_utc(self):
        # An offset-carrying value is converted to the equivalent UTC instant,
        # and the result is expressed with a zero UTC offset.
        parsed = parse_db_time("2026-01-01T12:00:00+05:00")
        assert parsed == datetime(2026, 1, 1, 7, 0, 0, tzinfo=timezone.utc)
        assert parsed.utcoffset() == timedelta(0)
