"""
tests/mcp_server/test_db.py
==========================
Unit tests for the diary database bridge layer (mcp_server/db.py).
Tests all CRUD operations, schema migrations, unique constraints, FTS5 indexes,
and error handling paths using an in-memory database fixture.
"""

from __future__ import annotations
import sqlite3
import pytest
from pathlib import Path

from diary_core.schema import RawEntry, SummaryUnit, EntityProfile
from mcp_server.db import (
    DatabaseError,
    get_db_connection,
    init_db,
    generate_entry_id,
    upsert_raw_entry,
    get_raw_entry_by_id,
    get_raw_entries_by_date,
    upsert_summary,
    get_summary,
    list_summaries,
    upsert_entity_profile,
    get_entity_profile,
    list_entity_profiles,
    rebuild_fts_index,
)


@pytest.fixture
def db_conn():
    """Fixture that initializes a clean in-memory database connection for each test.

    Yields:
        An active sqlite3.Connection with tables created and WAL pragmas set.
    """
    conn = get_db_connection(":memory:")
    init_db(conn)
    yield conn
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Happy Path Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_init_db_creates_tables(db_conn):
    """Happy Path: Verifies that init_db creates all necessary tables and metadata."""
    cursor = db_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    
    assert "meta" in tables
    assert "raw_entry" in tables
    assert "summary" in tables
    assert "entity_profile" in tables
    assert "content_fts" in tables  # FTS5 virtual tables are shown as tables in SQLite master


def test_upsert_and_get_raw_entry(db_conn):
    """Happy Path: Tests inserting a RawEntry and retrieving it by ID or Date."""
    entry = RawEntry(
        date="2026-06-15",
        time="05:30:00",
        source="session",
        source_file="test_session.json",
        text="Had a great session explaining databases.",
        role="user"
    )
    
    # 1. Test insertion & ID derivation
    entry_id = upsert_raw_entry(db_conn, entry)
    assert len(entry_id) == 16
    assert entry_id == generate_entry_id(entry)
    
    # 2. Test lookup by ID
    retrieved = get_raw_entry_by_id(db_conn, entry_id)
    assert retrieved is not None
    assert retrieved.date == "2026-06-15"
    assert retrieved.time == "05:30:00"
    assert retrieved.source == "session"
    assert retrieved.source_file == "test_session.json"
    assert retrieved.text == "Had a great session explaining databases."
    assert retrieved.role == "user"
    
    # 3. Test lookup by Date
    entries_list = get_raw_entries_by_date(db_conn, "2026-06-15")
    assert len(entries_list) == 1
    assert entries_list[0].text == "Had a great session explaining databases."


def test_upsert_and_get_summary(db_conn):
    """Happy Path: Verifies upserting a SummaryUnit and listing/retrieving it."""
    summary = SummaryUnit(
        unit="day",
        period_start="2026-06-15",
        period_end="2026-06-15",
        summary="A highly productive day teaching SQL concepts.",
        emotional_tone="energetic",
        key_events=["Explained WAL mode", "Created db.py file"],
        energy_level=5
    )
    
    # Upsert
    upsert_summary(db_conn, summary)
    
    # Retrieve
    retrieved = get_summary(db_conn, "day", "2026-06-15")
    assert retrieved is not None
    assert retrieved.unit == "day"
    assert retrieved.period_start == "2026-06-15"
    assert retrieved.period_end == "2026-06-15"
    assert retrieved.summary == "A highly productive day teaching SQL concepts."
    assert retrieved.emotional_tone == "energetic"
    assert retrieved.key_events == ["Explained WAL mode", "Created db.py file"]
    assert retrieved.energy_level == 5
    
    # Verify lists
    assert list_summaries(db_conn, "day") == ["2026-06-15"]


def test_upsert_and_get_entity_profile(db_conn):
    """Happy Path: Verifies upserting and loading psychological EntityProfiles."""
    profile = EntityProfile(
        id="john_doe",
        display_name="John Doe",
        aliases=["John", "Johnny"],
        role_in_authors_life="Collaborator"
    )
    
    # Upsert
    upsert_entity_profile(db_conn, profile)
    
    # Retrieve
    retrieved = get_entity_profile(db_conn, "john_doe")
    assert retrieved is not None
    assert retrieved.id == "john_doe"
    assert retrieved.display_name == "John Doe"
    assert retrieved.aliases == ["John", "Johnny"]
    assert retrieved.role_in_authors_life == "Collaborator"
    
    # List profiles
    profiles = list_entity_profiles(db_conn)
    assert len(profiles) == 1
    assert profiles[0].id == "john_doe"


def test_rebuild_fts_index_and_search(db_conn):
    """Happy Path: Verifies rebuilding FTS index and running a keyword query."""
    # Insert raw entry and summary
    upsert_raw_entry(db_conn, RawEntry("2026-06-15", "08:00:00", "test", "f.json", "I love designing databases.", "user"))
    upsert_summary(db_conn, SummaryUnit("day", "2026-06-15", "2026-06-15", summary="Reflective session on code standards."))
    
    # Build FTS index
    rebuild_fts_index(db_conn)
    
    # Search for raw entry content
    cursor = db_conn.execute("SELECT source_type, source_id, date, content FROM content_fts WHERE content MATCH 'designing'")
    results = cursor.fetchall()
    assert len(results) == 1
    assert results[0][0] == "raw"
    assert results[0][2] == "2026-06-15"
    assert "designing" in results[0][3]

    # Search for summary content
    cursor = db_conn.execute("SELECT source_type, source_id, date, content FROM content_fts WHERE content MATCH 'standards'")
    results = cursor.fetchall()
    assert len(results) == 1
    assert results[0][0] == "summary_day"
    assert results[0][1] == "day:2026-06-15"


# ─────────────────────────────────────────────────────────────────────────────
#  Edge Cases
# ─────────────────────────────────────────────────────────────────────────────

def test_upsert_overwrite_raw_entry(db_conn):
    """Edge Case: Verifies that inserting the exact same RawEntry does not double entries."""
    entry = RawEntry("2026-06-15", "09:00:00", "owui", "f.json", "Entry text", "user")
    
    # Write twice
    id_1 = upsert_raw_entry(db_conn, entry)
    id_2 = upsert_raw_entry(db_conn, entry)
    
    assert id_1 == id_2
    
    # Fetch list - should still have length 1
    entries_list = get_raw_entries_by_date(db_conn, "2026-06-15")
    assert len(entries_list) == 1
    assert entries_list[0].text == "Entry text"


def test_upsert_overwrite_summary(db_conn):
    """Edge Case: Verifies that upserting a summary overwrites the old one (Replacement)."""
    sum_1 = SummaryUnit("day", "2026-06-15", "2026-06-15", summary="First Summary")
    sum_2 = SummaryUnit("day", "2026-06-15", "2026-06-15", summary="Updated Summary")
    
    upsert_summary(db_conn, sum_1)
    upsert_summary(db_conn, sum_2)
    
    # Fetch and check if replacement occurred
    retrieved = get_summary(db_conn, "day", "2026-06-15")
    assert retrieved is not None
    assert retrieved.summary == "Updated Summary"


def test_get_non_existent_records(db_conn):
    """Edge Case: Verifies correct fallback behavior for missing database items."""
    assert get_raw_entry_by_id(db_conn, "missing_hash") is None
    assert get_raw_entries_by_date(db_conn, "2026-12-31") == []
    assert get_summary(db_conn, "day", "2026-12-31") is None
    assert get_entity_profile(db_conn, "non_existent") is None
    assert list_summaries(db_conn, "month") == []


# ─────────────────────────────────────────────────────────────────────────────
#  Error Cases
# ─────────────────────────────────────────────────────────────────────────────

def test_database_operations_on_closed_connection():
    """Error Case: Tests that database methods raise DatabaseError when connection is closed."""
    conn = get_db_connection(":memory:")
    init_db(conn)
    conn.close()
    
    entry = RawEntry("2026-06-15", "10:00:00", "test", "f.json", "text", "user")
    summary = SummaryUnit("day", "2026-06-15", "2026-06-15", "summary")
    profile = EntityProfile("id", "Name")
    
    with pytest.raises(DatabaseError):
        upsert_raw_entry(conn, entry)
        
    with pytest.raises(DatabaseError):
        get_raw_entry_by_id(conn, "some_id")
        
    with pytest.raises(DatabaseError):
        get_raw_entries_by_date(conn, "2026-06-15")
        
    with pytest.raises(DatabaseError):
        upsert_summary(conn, summary)
        
    with pytest.raises(DatabaseError):
        get_summary(conn, "day", "2026-06-15")
        
    with pytest.raises(DatabaseError):
        list_summaries(conn, "day")
        
    with pytest.raises(DatabaseError):
        upsert_entity_profile(conn, profile)
        
    with pytest.raises(DatabaseError):
        get_entity_profile(conn, "id")
        
    with pytest.raises(DatabaseError):
        list_entity_profiles(conn)
        
    with pytest.raises(DatabaseError):
        rebuild_fts_index(conn)
