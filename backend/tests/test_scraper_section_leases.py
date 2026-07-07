"""
Tests for the section-sync run leases in services/scraper/store.py —
acquire / heartbeat / stale steal / complete / fail, all guarded by
owner_token so a superseded worker can no longer mutate the active run.
"""
from datetime import timedelta

from config import Config
from db.pool import get_conn
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

    def test_complete_by_non_owner_is_a_noop(self, scraper_db):
        now = utcnow()
        run_id = acquire_section_run(SECTION, 1, "owner-a", now, STALE)
        complete_section_run(SECTION, "owner-b", now + timedelta(seconds=60))
        assert _run_row(run_id)["status"] == "running"
