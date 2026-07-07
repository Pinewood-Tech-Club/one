"""
Tests for db/job_leases.py — the per-user Schoology refresh lease that
deduplicates refresh work across processes.

Covers: fresh acquire, contention while held, expiry-based steal, and the
owner_token guard on release.
"""
from datetime import timedelta

from db.job_leases import (
    acquire_schoology_refresh_lease,
    release_schoology_refresh_lease,
    utcnow,
)

TTL = 300
USER = 1


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

    def test_release_by_non_owner_is_a_noop(self, main_db):
        # The owner_token guard: a stale/foreign owner must not release
        # someone else's active lease.
        now = utcnow()
        acquire_schoology_refresh_lease(USER, "owner-a", now, TTL)
        release_schoology_refresh_lease(USER, "owner-b")
        assert acquire_schoology_refresh_lease(USER, "owner-c", now + timedelta(seconds=1), TTL) is False

    def test_original_owner_cannot_release_after_steal(self, main_db):
        # After expiry + steal, the original owner's token no longer matches:
        # its release must not free the new owner's lease.
        now = utcnow()
        acquire_schoology_refresh_lease(USER, "owner-a", now, TTL)
        steal_time = now + timedelta(seconds=TTL + 1)
        assert acquire_schoology_refresh_lease(USER, "owner-b", steal_time, TTL) is True
        release_schoology_refresh_lease(USER, "owner-a")
        assert acquire_schoology_refresh_lease(USER, "owner-c", steal_time + timedelta(seconds=1), TTL) is False
