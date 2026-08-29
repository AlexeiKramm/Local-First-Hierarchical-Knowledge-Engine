"""
tests/diary_core/test_progress_manager.py
=============================================
Unit tests for the DB-backed ProgressManager.

Uses an in-memory SQLite database fixture mirroring the pattern from
tests/mcp_server/test_db.py to test all CRUD operations, edge cases,
and error handling without requiring a real database file.
"""

from __future__ import annotations

import sqlite3
import pytest

from diary_core.schema import SummaryUnit
from diary_core.progress_manager import ProgressManager, ProgressError
from mcp_server.db import get_db_connection, init_db


@pytest.fixture
def db_conn():
    """Create a clean in-memory database for each test."""
    conn = get_db_connection(":memory:")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def pm(db_conn):
    """Create a ProgressManager backed by an in-memory database."""
    return ProgressManager(db_conn=db_conn)


def _make_summary(
    unit: str = "day",
    period_start: str = "2026-06-01",
    period_end: str = "2026-06-01",
    summary: str = "Test summary.",
) -> SummaryUnit:
    """Helper to construct a minimal SummaryUnit for testing."""
    return SummaryUnit(
        unit=unit,
        period_start=period_start,
        period_end=period_end,
        summary=summary,
    )


# ── Happy Path Tests ────────────────────────────────────────────────────────


class TestSaveAndLoad:
    """Happy path: save and load a summary."""

    def test_save_and_load_unit(self, pm: ProgressManager):
        unit = _make_summary()
        pm.save_unit(unit)
        loaded = pm.load_unit("day", "2026-06-01")
        assert loaded is not None
        assert loaded.summary == "Test summary."
        assert loaded.unit == "day"
        assert loaded.period_start == "2026-06-01"

    def test_save_and_load_all_levels(self, pm: ProgressManager):
        """Save one summary at each level and verify each loads correctly."""
        levels = [
            ("day", "2026-06-01", "2026-06-01"),
            ("week", "2026-06-01", "2026-06-07"),
            ("month", "2026-06", "2026-06-30"),
            ("year", "2026", "2026-12-31"),
        ]
        for level, start, end in levels:
            s = _make_summary(unit=level, period_start=start, period_end=end)
            pm.save_unit(s)
            loaded = pm.load_unit(level, start)
            assert loaded is not None
            assert loaded.unit == level


class TestLoadAllUnits:
    """Happy path: load all summaries for a given level."""

    def test_load_all_units_sorted(self, pm: ProgressManager):
        dates = ["2026-06-01", "2026-06-02", "2026-06-03"]
        for d in dates:
            pm.save_unit(_make_summary(period_start=d))
        all_units = pm.load_all_units("day")
        assert len(all_units) == 3
        assert [u.period_start for u in all_units] == dates

    def test_load_all_units_multiple_levels(self, pm: ProgressManager):
        pm.save_unit(_make_summary(unit="day", period_start="2026-06-01"))
        pm.save_unit(_make_summary(unit="week", period_start="2026-W23"))
        pm.save_unit(_make_summary(unit="month", period_start="2026-06"))
        days = pm.load_all_units("day")
        weeks = pm.load_all_units("week")
        months = pm.load_all_units("month")
        assert len(days) == 1
        assert len(weeks) == 1
        assert len(months) == 1
        assert days[0].unit == "day"
        assert weeks[0].unit == "week"
        assert months[0].unit == "month"


class TestIsDone:
    """Happy path: is_done correctly reflects saved state."""

    def test_is_done_after_save(self, pm: ProgressManager):
        assert pm.is_done("day", "2026-06-01") is False
        pm.save_unit(_make_summary(period_start="2026-06-01"))
        assert pm.is_done("day", "2026-06-01") is True

    def test_is_done_multiple_levels(self, pm: ProgressManager):
        pm.save_unit(_make_summary(unit="day", period_start="2026-06-01"))
        pm.save_unit(_make_summary(unit="week", period_start="2026-W23"))
        assert pm.is_done("day", "2026-06-01") is True
        assert pm.is_done("day", "2026-06-02") is False
        assert pm.is_done("week", "2026-W23") is True
        assert pm.is_done("month", "2026-06") is False


class TestCompletedKeys:
    """Happy path: completed_keys returns correct sets."""

    def test_completed_keys_after_save(self, pm: ProgressManager):
        pm.save_unit(_make_summary(period_start="2026-06-01"))
        pm.save_unit(_make_summary(period_start="2026-06-02"))
        keys = pm.completed_keys("day")
        assert keys == {"2026-06-01", "2026-06-02"}

    def test_completed_keys_by_level(self, pm: ProgressManager):
        pm.save_unit(_make_summary(unit="day", period_start="2026-06-01"))
        pm.save_unit(_make_summary(unit="week", period_start="2026-W23"))
        assert pm.completed_keys("day") == {"2026-06-01"}
        assert pm.completed_keys("week") == {"2026-W23"}
        assert pm.completed_keys("month") == set()


class TestInvalidateUnit:
    """Happy path: invalidate_unit removes summaries."""

    def test_invalidate_removes_summary(self, pm: ProgressManager):
        pm.save_unit(_make_summary(period_start="2026-06-01"))
        assert pm.is_done("day", "2026-06-01") is True
        result = pm.invalidate_unit("day", "2026-06-01")
        assert result is True
        assert pm.is_done("day", "2026-06-01") is False
        assert pm.load_unit("day", "2026-06-01") is None

    def test_invalidate_only_affected_level(self, pm: ProgressManager):
        pm.save_unit(_make_summary(unit="day", period_start="2026-06-01"))
        pm.save_unit(_make_summary(unit="week", period_start="2026-W23"))
        pm.invalidate_unit("day", "2026-06-01")
        assert pm.is_done("day", "2026-06-01") is False
        assert pm.is_done("week", "2026-W23") is True


class TestReset:
    """Happy path: reset removes summaries."""

    def test_reset_all(self, pm: ProgressManager):
        pm.save_unit(_make_summary(unit="day", period_start="2026-06-01"))
        pm.save_unit(_make_summary(unit="week", period_start="2026-W23"))
        pm.save_unit(_make_summary(unit="month", period_start="2026-06"))
        pm.reset()
        assert pm.load_all_units("day") == []
        assert pm.load_all_units("week") == []
        assert pm.load_all_units("month") == []

    def test_reset_single_level(self, pm: ProgressManager):
        pm.save_unit(_make_summary(unit="day", period_start="2026-06-01"))
        pm.save_unit(_make_summary(unit="week", period_start="2026-W23"))
        pm.reset("day")
        assert pm.load_all_units("day") == []
        assert len(pm.load_all_units("week")) == 1


class TestStatusSummary:
    """Happy path: status_summary returns correct counts."""

    def test_status_summary_counts(self, pm: ProgressManager):
        pm.save_unit(_make_summary(unit="day", period_start="2026-06-01"))
        pm.save_unit(_make_summary(unit="day", period_start="2026-06-02"))
        pm.save_unit(_make_summary(unit="day", period_start="2026-06-03"))
        pm.save_unit(_make_summary(unit="week", period_start="2026-W23"))
        pm.save_unit(_make_summary(unit="week", period_start="2026-W24"))
        pm.save_unit(_make_summary(unit="month", period_start="2026-06"))
        status = pm.status_summary()
        assert status.get("day") == 3
        assert status.get("week") == 2
        assert status.get("month") == 1

    def test_status_summary_empty(self, pm: ProgressManager):
        assert pm.status_summary() == {}


# ── Edge Case Tests ─────────────────────────────────────────────────────────


class TestLoadUnitNotFound:
    """Edge cases: loading non-existent units."""

    def test_load_unit_not_found(self, pm: ProgressManager):
        result = pm.load_unit("day", "2099-01-01")
        assert result is None

    def test_load_all_units_empty(self, pm: ProgressManager):
        assert pm.load_all_units("year") == []


class TestIsDoneNotFound:
    """Edge cases: checking non-existent units."""

    def test_is_done_not_found(self, pm: ProgressManager):
        assert pm.is_done("day", "2099-01-01") is False
        assert pm.is_done("week", "2099-W01") is False
        assert pm.is_done("month", "2099-01") is False
        assert pm.is_done("year", "2099") is False


class TestCompletedKeysEmpty:
    """Edge cases: completed_keys on empty levels."""

    def test_completed_keys_empty(self, pm: ProgressManager):
        assert pm.completed_keys("day") == set()
        assert pm.completed_keys("year") == set()


class TestResetEmpty:
    """Edge cases: reset on empty database."""

    def test_reset_on_empty_db(self, pm: ProgressManager):
        pm.reset()
        assert pm.status_summary() == {}
        pm.reset("day")
        assert pm.status_summary() == {}

    def test_invalidate_missing_key(self, pm: ProgressManager):
        result = pm.invalidate_unit("day", "2099-01-01")
        assert result is False


# ── Error Case Tests ─────────────────────────────────────────────────────────


class TestClosedConnection:
    """Error cases: operations on a closed connection."""

    def test_save_unit_closed_connection(self, db_conn):
        db_conn.close()
        pm = ProgressManager(db_conn=db_conn)
        with pytest.raises(ProgressError):
            pm.save_unit(_make_summary())

    def test_load_unit_closed_connection(self, db_conn):
        db_conn.close()
        pm = ProgressManager(db_conn=db_conn)
        with pytest.raises(ProgressError):
            pm.load_unit("day", "2026-06-01")

    def test_load_all_units_closed_connection(self, db_conn):
        db_conn.close()
        pm = ProgressManager(db_conn=db_conn)
        with pytest.raises(ProgressError):
            pm.load_all_units("day")

    def test_completed_keys_closed_connection(self, db_conn):
        db_conn.close()
        pm = ProgressManager(db_conn=db_conn)
        with pytest.raises(ProgressError):
            pm.completed_keys("day")

    def test_reset_closed_connection(self, db_conn):
        db_conn.close()
        pm = ProgressManager(db_conn=db_conn)
        with pytest.raises(ProgressError):
            pm.reset()


class TestGracefulDegradation:
    """Error cases that should degrade gracefully (no exception)."""

    def test_is_done_closed_connection(self, db_conn):
        db_conn.close()
        pm = ProgressManager(db_conn=db_conn)
        assert pm.is_done("day", "2026-06-01") is False

    def test_invalidate_unit_closed_connection(self, db_conn):
        db_conn.close()
        pm = ProgressManager(db_conn=db_conn)
        assert pm.invalidate_unit("day", "2026-06-01") is False

    def test_status_summary_closed_connection(self, db_conn):
        db_conn.close()
        pm = ProgressManager(db_conn=db_conn)
        assert pm.status_summary() == {}
