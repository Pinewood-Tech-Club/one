"""
Tests for the section-sync run leases in services/scraper/store.py —
acquire / heartbeat / stale steal / complete / fail, all guarded by
owner_token so a superseded worker can no longer mutate the active run.
"""
from datetime import timedelta

import pytest

from db.pool import get_conn
from config import Config
from services.scraper.store import (
    acquire_section_run,
    complete_section_run,
    fail_section_run,
    heartbeat_section_run,
    parse_db_time,
    to_db_time,
    utcnow,
)

SECTION = "section-123"
STALE = 300


def _run_row(run_id: int) -> dict:
    conn = get_conn(Config.SCRAPER_DB_PATH)
    row = conn.execute("SELECT * FROM section_sync_runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row)


def _insert_section(now) -> None:
    conn = get_conn(Config.SCRAPER_DB_PATH)
    conn.execute(
        """
        INSERT INTO sections (section_id, title, raw_json, raw_hash, last_discovered_at)
        VALUES (?, 'Test Section', '{}', 'hash', ?)
        """,
        (SECTION, to_db_time(now)),
    )
    conn.commit()


def _section_row(section_id: str = SECTION) -> dict:
    conn = get_conn(Config.SCRAPER_DB_PATH)
    return dict(
        conn.execute("SELECT * FROM sections WHERE section_id = ?", (section_id,)).fetchone()
    )


def _running_rows(section_id: str = SECTION) -> list[dict]:
    conn = get_conn(Config.SCRAPER_DB_PATH)
    rows = conn.execute(
        "SELECT * FROM section_sync_runs WHERE section_id = ? AND status = 'running'",
        (section_id,),
    ).fetchall()
    return [dict(r) for r in rows]


class TestAcquire:
    def test_acquire_fresh_section_succeeds(self, scraper_db):
        now = utcnow()
        run_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        assert run_id is not None
        row = _run_row(run_id)
        assert row["status"] == "running"
        assert row["owner_token"] == "owner-a"
        assert row["attempt_count"] == 1

    def test_second_acquire_blocked_while_heartbeat_fresh(self, scraper_db):
        now = utcnow()
        assert acquire_section_run(SECTION, 1, "owner-a", now, STALE) is not None
        assert acquire_section_run(SECTION, 2, "owner-b", now + timedelta(seconds=STALE - 1), STALE) is None

    def test_acquire_different_sections_independent(self, scraper_db):
        now = utcnow()
        assert acquire_section_run("section-1", 1, "owner-a", now, STALE) is not None
        assert acquire_section_run("section-2", 1, "owner-a", now, STALE) is not None

    def test_acquire_records_started_and_heartbeat_at_now(self, scraper_db):
        # Kills mutants that write a wrong timestamp into run_started_at /
        # heartbeat_at on INSERT (e.g. using a constant, or swapping columns).
        now = utcnow()
        run_id = acquire_section_run(SECTION, 7, "owner-a", now, STALE)
        row = _run_row(run_id)
        assert parse_db_time(row["run_started_at"]) == now
        assert parse_db_time(row["heartbeat_at"]) == now
        assert row["credential_user_id"] == 7
        assert row["finished_at"] is None
        assert row["last_error"] is None

    def test_blocked_acquire_leaves_exactly_one_untouched_running_run(self, scraper_db):
        # A blocked (fresh-lease) acquire must `return None` WITHOUT inserting a
        # second run and WITHOUT touching the incumbent. Kills a mutant that
        # drops the early `return None` and falls through to the INSERT.
        now = utcnow()
        first = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        blocked = acquire_section_run(SECTION, 2, "owner-b", now + timedelta(seconds=STALE - 1), STALE)
        assert blocked is None
        running = _running_rows()
        assert len(running) == 1
        assert running[0]["id"] == first
        assert running[0]["owner_token"] == "owner-a"
        assert parse_db_time(running[0]["heartbeat_at"]) == now

    def test_acquire_at_exact_staleness_boundary_is_blocked(self, scraper_db):
        # heartbeat_at(now) >= steal_time - STALE  ==>  now >= now  ==> True.
        # Exactly at the deadline the lease is still fresh, so the steal is
        # refused. Kills the `>=` -> `>` boundary mutant.
        now = utcnow()
        first = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        assert acquire_section_run(SECTION, 2, "owner-b", now + timedelta(seconds=STALE), STALE) is None
        # Incumbent still owns it and was never failed.
        assert _run_row(first)["status"] == "running"

    def test_acquire_one_second_past_boundary_steals(self, scraper_db):
        # The mirror of the boundary test: one second later the lease is stale
        # and the steal succeeds. Together these pin the exact `>=` threshold.
        now = utcnow()
        first = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        stolen = acquire_section_run(SECTION, 2, "owner-b", now + timedelta(seconds=STALE + 1), STALE)
        assert stolen is not None and stolen != first
        assert _run_row(first)["status"] == "failed"


class TestHeartbeat:
    def test_heartbeat_by_owner_extends_lease(self, scraper_db):
        now = utcnow()
        run_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        later = now + timedelta(seconds=STALE - 10)
        heartbeat_section_run(SECTION, "owner-a", later)
        assert parse_db_time(_run_row(run_id)["heartbeat_at"]) == later
        # A would-be thief just after the ORIGINAL deadline is now blocked,
        # because the heartbeat pushed the staleness window forward.
        assert acquire_section_run(SECTION, 2, "owner-b", now + timedelta(seconds=STALE + 1), STALE) is None

    def test_heartbeat_by_non_owner_is_a_noop(self, scraper_db):
        now = utcnow()
        run_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        heartbeat_section_run(SECTION, "owner-b", now + timedelta(seconds=60))
        assert parse_db_time(_run_row(run_id)["heartbeat_at"]) == now


class TestStaleSteal:
    def test_stale_lease_is_stolen_and_old_run_failed(self, scraper_db):
        now = utcnow()
        old_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        steal_time = now + timedelta(seconds=STALE + 1)
        new_id = acquire_section_run(SECTION, 2, "owner-b", steal_time, STALE)
        assert new_id is not None and new_id != old_id

        old = _run_row(old_id)
        assert old["status"] == "failed"
        assert old["last_error"] == "stale lease replaced"
        assert old["finished_at"] is not None

        new = _run_row(new_id)
        assert new["status"] == "running"
        assert new["owner_token"] == "owner-b"
        assert new["attempt_count"] == 2  # attempt counter carries across steals

    def test_attempt_count_increments_monotonically_across_repeated_steals(self, scraper_db):
        # attempt_count = MAX(attempt_count) + 1 across the section's history.
        # Kills mutants on the `+ 1`, on MAX (e.g. -> MIN), and on the
        # COALESCE(..., 0) seed.
        now = utcnow()
        ids = []
        for i in range(3):
            when = now + timedelta(seconds=(STALE + 1) * i)
            ids.append(acquire_section_run(SECTION, 1, f"owner-{i}", when, STALE))
        assert [_run_row(rid)["attempt_count"] for rid in ids] == [1, 2, 3]

    def test_stolen_run_starts_fresh_at_steal_time(self, scraper_db):
        # The replacement run's run_started_at/heartbeat_at reflect the steal
        # moment, not the original acquisition. Kills timestamp-source mutants.
        now = utcnow()
        acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        steal_time = now + timedelta(seconds=STALE + 1)
        new_id = acquire_section_run(SECTION, 2, "owner-b", steal_time, STALE)
        new = _run_row(new_id)
        assert parse_db_time(new["run_started_at"]) == steal_time
        assert parse_db_time(new["heartbeat_at"]) == steal_time
        assert new["finished_at"] is None

    def test_original_owner_cannot_heartbeat_complete_or_fail_after_steal(self, scraper_db):
        # The owner_token + status='running' guard: once superseded, every
        # mutation from the original owner must affect 0 rows.
        now = utcnow()
        old_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        steal_time = now + timedelta(seconds=STALE + 1)
        new_id = acquire_section_run(SECTION, 2, "owner-b", steal_time, STALE)

        later = steal_time + timedelta(seconds=30)
        heartbeat_section_run(SECTION, "owner-a", later)
        complete_section_run(SECTION, "owner-a", later)
        fail_section_run(SECTION, "owner-a", later, "zombie error")

        new = _run_row(new_id)
        assert new["status"] == "running"
        assert parse_db_time(new["heartbeat_at"]) == steal_time
        assert new["finished_at"] is None

        old = _run_row(old_id)
        assert old["status"] == "failed"
        assert old["last_error"] == "stale lease replaced"  # not overwritten by zombie


class TestCompleteAndFail:
    def test_complete_by_owner_marks_run_and_section(self, scraper_db):
        now = utcnow()
        _insert_section(now)
        run_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        done = now + timedelta(seconds=60)
        complete_section_run(SECTION, "owner-a", done)

        row = _run_row(run_id)
        assert row["status"] == "completed"
        assert parse_db_time(row["finished_at"]) == done

        conn = get_conn(Config.SCRAPER_DB_PATH)
        section = dict(conn.execute("SELECT * FROM sections WHERE section_id = ?", (SECTION,)).fetchone())
        assert parse_db_time(section["last_scraped_at"]) == done
        assert parse_db_time(section["last_successful_sync_at"]) == done

    def test_completed_section_can_be_reacquired_immediately(self, scraper_db):
        now = utcnow()
        acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        complete_section_run(SECTION, "owner-a", now + timedelta(seconds=60))
        assert acquire_section_run(SECTION, 2, "owner-b", now + timedelta(seconds=61), STALE) is not None

    def test_fail_by_owner_records_error(self, scraper_db):
        now = utcnow()
        run_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        fail_section_run(SECTION, "owner-a", now + timedelta(seconds=60), "boom")
        row = _run_row(run_id)
        assert row["status"] == "failed"
        assert row["last_error"] == "boom"

    def test_fail_truncates_long_error(self, scraper_db):
        now = utcnow()
        run_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        fail_section_run(SECTION, "owner-a", now + timedelta(seconds=60), "x" * 5000)
        assert len(_run_row(run_id)["last_error"]) == 1000

    def test_complete_by_owner_stamps_heartbeat_on_the_run(self, scraper_db):
        # complete_section_run bumps heartbeat_at as well as finished_at.
        # Kills a mutant that drops the heartbeat_at assignment on completion.
        now = utcnow()
        acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        done = now + timedelta(seconds=60)
        run_id = _running_rows()[0]["id"]
        complete_section_run(SECTION, "owner-a", done)
        row = _run_row(run_id)
        assert parse_db_time(row["heartbeat_at"]) == done
        assert parse_db_time(row["finished_at"]) == done

    def test_second_complete_is_a_noop_once_run_left_running(self, scraper_db):
        # The `status = 'running'` guard means a run can only be completed once;
        # a redundant completion must not re-stamp finished_at. Kills a mutant
        # that removes the status predicate from complete's WHERE clause.
        now = utcnow()
        run_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        first_done = now + timedelta(seconds=60)
        complete_section_run(SECTION, "owner-a", first_done)
        complete_section_run(SECTION, "owner-a", now + timedelta(seconds=600))
        row = _run_row(run_id)
        assert row["status"] == "completed"
        assert parse_db_time(row["finished_at"]) == first_done

    def test_fail_by_owner_stamps_finished_and_heartbeat(self, scraper_db):
        # fail_section_run stamps finished_at and heartbeat_at, not just status.
        now = utcnow()
        run_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        when = now + timedelta(seconds=60)
        fail_section_run(SECTION, "owner-a", when, "boom")
        row = _run_row(run_id)
        assert parse_db_time(row["finished_at"]) == when
        assert parse_db_time(row["heartbeat_at"]) == when

    def test_second_fail_is_a_noop_once_run_left_running(self, scraper_db):
        # `status = 'running'` guard on fail: a failed run cannot be re-failed
        # with a different error/timestamp. Kills the status-predicate mutant.
        now = utcnow()
        run_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        fail_section_run(SECTION, "owner-a", now + timedelta(seconds=60), "first")
        fail_section_run(SECTION, "owner-a", now + timedelta(seconds=600), "second")
        assert _run_row(run_id)["last_error"] == "first"

    def test_fail_error_kept_verbatim_below_limit(self, scraper_db):
        # Complements test_fail_truncates_long_error: an error at exactly the
        # 1000-char limit is stored whole, pinning the [:1000] slice from below.
        now = utcnow()
        run_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        fail_section_run(SECTION, "owner-a", now + timedelta(seconds=60), "e" * 1000)
        assert _run_row(run_id)["last_error"] == "e" * 1000

    def test_complete_by_non_owner_is_a_noop(self, scraper_db):
        now = utcnow()
        run_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        complete_section_run(SECTION, "owner-b", now + timedelta(seconds=60))
        assert _run_row(run_id)["status"] == "running"

    def test_complete_by_non_owner_does_not_finish_the_run(self, scraper_db):
        # Tighten the non-owner noop: the incumbent run must stay pristine —
        # no finished_at, heartbeat unmoved. Kills owner_token-guard mutants
        # on complete's section_sync_runs UPDATE.
        now = utcnow()
        run_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        complete_section_run(SECTION, "owner-b", now + timedelta(seconds=60))
        row = _run_row(run_id)
        assert row["finished_at"] is None
        assert parse_db_time(row["heartbeat_at"]) == now

    def test_fail_by_non_owner_is_a_noop(self, scraper_db):
        # Symmetric owner_token guard for fail_section_run.
        now = utcnow()
        run_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        fail_section_run(SECTION, "owner-b", now + timedelta(seconds=60), "intruder")
        row = _run_row(run_id)
        assert row["status"] == "running"
        assert row["last_error"] is None
        assert row["finished_at"] is None


class TestSectionStampOwnership:
    """
    The sections-table stamp (last_scraped_at / last_successful_sync_at) written
    by complete_section_run MUST be gated on run ownership, exactly like the
    section_sync_runs UPDATE. On this base branch it is NOT: the second UPDATE in
    complete_section_run is guarded only by `WHERE section_id = ?`, so ANY caller
    (a non-owner, or an owner whose lease was already stolen) can forge a
    "successful sync" stamp for a section it never actually scraped.

    These guards are therefore RED on this branch and marked strict xfail. When
    PR #2 (fix/scraper-lease-ownership) merges and the owner check is added, they
    will XPASS -> strict xfail turns the XPASS into a FAILURE, which is the signal
    to delete these markers.
    """

    @pytest.mark.xfail(
        strict=True,
        reason="regression guard for the sections-stamp owner check; currently RED "
        "because the fix is in unmerged PR #2 (fix/scraper-lease-ownership) — "
        "remove this xfail when #2 merges",
    )
    def test_non_owner_complete_must_not_stamp_sections_table(self, scraper_db):
        now = utcnow()
        _insert_section(now)
        acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        # owner-b never held this lease; its completion must be a total noop,
        # including on the sections table.
        complete_section_run(SECTION, "owner-b", now + timedelta(seconds=60))
        section = _section_row()
        assert section["last_successful_sync_at"] is None
        assert section["last_scraped_at"] is None

    @pytest.mark.xfail(
        strict=True,
        reason="regression guard for the sections-stamp owner check; currently RED "
        "because the fix is in unmerged PR #2 (fix/scraper-lease-ownership) — "
        "remove this xfail when #2 merges",
    )
    def test_superseded_owner_complete_must_not_stamp_sections_table(self, scraper_db):
        # owner-a's lease is stolen by owner-b; a late completion from the
        # zombie owner-a must not forge a successful-sync stamp on the section.
        now = utcnow()
        _insert_section(now)
        acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        steal_time = now + timedelta(seconds=STALE + 1)
        acquire_section_run(SECTION, 2, "owner-b", steal_time, STALE)
        complete_section_run(SECTION, "owner-a", steal_time + timedelta(seconds=30))
        section = _section_row()
        assert section["last_successful_sync_at"] is None
        assert section["last_scraped_at"] is None
