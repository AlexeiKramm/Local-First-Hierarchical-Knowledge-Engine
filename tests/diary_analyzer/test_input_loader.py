"""Tests for diary_core/input_loader.py — database-backed loading."""

from __future__ import annotations

import sqlite3

import pytest

from diary_core.input_loader import load_from_db
from diary_core.schema import RawEntry
from mcp_server.db import DatabaseError, get_db_connection, init_db, upsert_raw_entry


@pytest.fixture
def db_conn():
    conn = get_db_connection(":memory:")
    init_db(conn)
    yield conn
    conn.close()


def _populate(conn: sqlite3.Connection, entries: list[RawEntry]) -> None:
    for e in entries:
        upsert_raw_entry(conn, e)


class TestLoadFromDb:
    """Tests for load_from_db()."""

    def test_loads_entries_across_multiple_dates(self, db_conn):
        _populate(db_conn, [
            RawEntry(date="2026-01-01", time="10:00:00", source="txt",
                     source_file="a.txt", text="Hello", role="user"),
            RawEntry(date="2026-01-01", time="11:00:00", source="txt",
                     source_file="a.txt", text="World", role="assistant"),
            RawEntry(date="2026-01-02", time="09:00:00", source="txt",
                     source_file="b.txt", text="Day two", role="user"),
        ])
        days = load_from_db(db_conn)
        assert len(days) == 2
        assert days[0].date == "2026-01-01"
        assert days[1].date == "2026-01-02"
        assert len(days[0].entries) == 2
        assert len(days[1].entries) == 1

    def test_returns_empty_list_for_empty_db(self, db_conn):
        assert load_from_db(db_conn) == []

    def test_filters_by_user_role(self, db_conn):
        _populate(db_conn, [
            RawEntry(date="2026-01-01", time="10:00:00", source="txt",
                     source_file="a.txt", text="User msg", role="user"),
            RawEntry(date="2026-01-01", time="11:00:00", source="txt",
                     source_file="a.txt", text="Assistant msg", role="assistant"),
        ])
        days = load_from_db(db_conn, roles=["user"])
        assert len(days) == 1
        assert len(days[0].entries) == 1
        assert days[0].entries[0].role == "user"

    def test_keeps_all_roles_when_none_specified(self, db_conn):
        _populate(db_conn, [
            RawEntry(date="2026-01-01", time="10:00:00", source="txt",
                     source_file="a.txt", text="User msg", role="user"),
            RawEntry(date="2026-01-01", time="11:00:00", source="txt",
                     source_file="a.txt", text="Assistant msg", role="assistant"),
        ])
        days = load_from_db(db_conn, roles=None)
        assert len(days) == 1
        assert len(days[0].entries) == 2

    def test_keeps_multiple_specified_roles(self, db_conn):
        _populate(db_conn, [
            RawEntry(date="2026-01-01", time="10:00:00", source="txt",
                     source_file="a.txt", text="User msg", role="user"),
            RawEntry(date="2026-01-01", time="11:00:00", source="txt",
                     source_file="a.txt", text="Assistant msg", role="assistant"),
        ])
        days = load_from_db(db_conn, roles=["user", "assistant"])
        assert len(days) == 1
        assert len(days[0].entries) == 2

    def test_handles_single_day(self, db_conn):
        _populate(db_conn, [
            RawEntry(date="2026-06-01", time="08:00:00", source="txt",
                     source_file="log.txt", text="Only day", role="user"),
        ])
        days = load_from_db(db_conn)
        assert len(days) == 1
        assert days[0].date == "2026-06-01"

    def test_raises_database_error_on_closed_connection(self):
        conn = get_db_connection(":memory:")
        init_db(conn)
        conn.close()
        with pytest.raises(DatabaseError):
            load_from_db(conn)

    def test_entries_ordered_by_time_within_date(self, db_conn):
        _populate(db_conn, [
            RawEntry(date="2026-01-01", time="12:00:00", source="txt",
                     source_file="a.txt", text="Noon", role="user"),
            RawEntry(date="2026-01-01", time="09:00:00", source="txt",
                     source_file="a.txt", text="Morning", role="user"),
            RawEntry(date="2026-01-01", time="15:00:00", source="txt",
                     source_file="a.txt", text="Afternoon", role="user"),
        ])
        days = load_from_db(db_conn)
        times = [e.time for e in days[0].entries]
        assert times == ["09:00:00", "12:00:00", "15:00:00"]
