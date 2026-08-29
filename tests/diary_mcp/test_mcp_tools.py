"""
tests/mcp_server/test_mcp_tools.py
==================================
Integration tests for MCP server tools that simulate an agent calling each tool.

Calls tool functions directly (not through FastMCP transport), monkey-patching
_get_db() to inject an in-memory database. Verifies full JSON response structure
for happy-path, not-found, and DB-failure scenarios.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from diary_core.schema import (
    EntityProfile,
    RawEntry,
    SummaryUnit,
    TimestampedKnowledge,
)
from mcp_server.db import (
    get_db_connection,
    init_db,
    rebuild_fts_index,
    upsert_entity_profile,
    upsert_raw_entry,
    upsert_summary,
)
from mcp_server.mcp_server import (
    get_closest_files,
    get_diary_architecture_help,
    get_entity_profile,
    get_raw_entry,
    get_summary,
    list_entities,
    list_raw_files,
    list_summary_files,
    search_full_text,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db_conn():
    """Create a clean in-memory database for each test."""
    conn = get_db_connection(":memory:")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def seeded_db(db_conn):
    """Populate the in-memory DB with representative test data across all tables."""
    _seed_raw_entries(db_conn)
    _seed_summaries(db_conn)
    _seed_entity_profiles(db_conn)
    return db_conn


# ── Seed Helpers ──────────────────────────────────────────────────────────────

def _seed_raw_entries(conn: sqlite3.Connection) -> None:
    """Insert raw entries across 3 dates for testing."""
    entries = [
        RawEntry("2026-06-15", "08:00:00", "session", "s1", "I love designing databases.", "user"),
        RawEntry("2026-06-15", "08:05:00", "session", "s1", "SQL is fun to work with.", "assistant"),
        RawEntry("2026-06-16", "09:00:00", "chat", "c1", "Today was a burnout day.", "user"),
        RawEntry("2026-06-17", "10:00:00", "chat", "c2", "Recovered and feeling better.", "user"),
    ]
    for e in entries:
        upsert_raw_entry(conn, e)


def _seed_summaries(conn: sqlite3.Connection) -> None:
    """Insert one summary at each level for testing."""
    summaries = [
        SummaryUnit("day", "2026-06-15", "2026-06-15",
                    summary="Productive day working on databases."),
        SummaryUnit("week", "2026-06-15", "2026-06-21",
                    summary="A week of coding and reflection."),
        SummaryUnit("month", "2026-06", "2026-06-30",
                    summary="June was a month of technical growth."),
        SummaryUnit("year", "2026", "2026-12-31",
                    summary="2026 was a transformative year."),
    ]
    for s in summaries:
        upsert_summary(conn, s)


def _seed_entity_profiles(conn: sqlite3.Connection) -> None:
    """Insert entity profiles with different mention counts for sort testing."""
    anna = EntityProfile(
        id="anna_korhonen",
        display_name="Anna Korhonen",
        aliases=["Anna", "Ankku"],
        role_in_authors_life="Friend",
        first_mentioned_in_diary="2024-03-01",
        relationship_arc_summary="Close friend who drifted apart.",
        arc_last_updated="2025-12-01",
        stable_facts={"mention_count": 3},
        timestamped_knowledge=[
            TimestampedKnowledge(
                valid_from="2024-03-01",
                source_entries=["2024-03-01", "2024-03-05"],
                content="Anna and I reconnected after college.",
                emotional_valence="warm",
                tags=["friend", "reconnection"],
            ),
            TimestampedKnowledge(
                valid_from="2025-06-15",
                source_entries=["2025-06-15"],
                content="Anna moved to another city.",
                emotional_valence="neutral",
                tags=["friend", "move"],
            ),
        ],
    )
    mikko = EntityProfile(
        id="mikko_virtanen",
        display_name="Mikko Virtanen",
        role_in_authors_life="Colleague",
        first_mentioned_in_diary="2025-01-10",
        relationship_arc_summary="Work colleague from the tech team.",
        timestamped_knowledge=[],
    )
    upsert_entity_profile(conn, anna)
    upsert_entity_profile(conn, mikko)


def _monkeypatch_db(monkeypatch, conn: sqlite3.Connection) -> None:
    """Replace _get_db() with a function returning the given test connection."""
    import mcp_server.mcp_server as mcp_server
    mcp_server._db_conn = None
    monkeypatch.setattr(mcp_server, "_get_db", lambda: conn)


def _is_error_response(output: str) -> bool:
    """Check if a tool output is an error string (plain 'ERROR:' or JSON with 'error' key)."""
    if output.startswith("ERROR:"):
        return True
    try:
        data = json.loads(output)
        return "error" in data
    except (json.JSONDecodeError, TypeError):
        return False


# ──────────────────────────────────────────────────────────────────────────────
#  U1: DB Failure Tests — Closed Connection + Connection Raises
# ──────────────────────────────────────────────────────────────────────────────

class TestDbFailure:
    """Tools return sensible error messages when the database is unavailable."""

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        self.monkeypatch = monkeypatch

    def _patch_closed(self, conn):
        """Patch _get_db to return a closed connection (tests DB-query failure)."""
        conn.close()
        _monkeypatch_db(self.monkeypatch, conn)

    def _patch_raises(self):
        """Patch _get_db to raise RuntimeError (tests connection-acquisition failure)."""
        import mcp_server.mcp_server as mcp_server
        mcp_server._db_conn = None

        def _raising():
            raise RuntimeError("Simulated connection failure")

        self.monkeypatch.setattr(mcp_server, "_get_db", _raising)

    # -- Closed connection tests --

    def test_list_entities_db_closed(self, db_conn):
        self._patch_closed(db_conn)
        result = list_entities()
        assert result.startswith("ERROR:"), f"Expected ERROR:, got: {result[:100]}"

    def test_get_entity_profile_db_closed(self, db_conn):
        self._patch_closed(db_conn)
        result = get_entity_profile("anna_korhonen")
        assert result.startswith("ERROR:"), f"Expected ERROR:, got: {result[:100]}"

    def test_get_summary_db_closed(self, db_conn):
        self._patch_closed(db_conn)
        result = get_summary("day", ["2026-06-15"])
        assert result.startswith("ERROR:"), f"Expected ERROR:, got: {result[:100]}"

    def test_list_summary_files_db_closed(self, db_conn):
        self._patch_closed(db_conn)
        result = list_summary_files("day")
        assert result.startswith("ERROR:"), f"Expected ERROR:, got: {result[:100]}"

    def test_list_raw_files_db_closed(self, db_conn):
        self._patch_closed(db_conn)
        result = list_raw_files()
        assert result.startswith("ERROR:"), f"Expected ERROR:, got: {result[:100]}"

    def test_get_raw_entry_db_closed(self, db_conn):
        self._patch_closed(db_conn)
        result = get_raw_entry(["2026-06-15"])
        assert result.startswith("ERROR:"), f"Expected ERROR:, got: {result[:100]}"

    def test_search_full_text_db_closed(self, db_conn):
        """search_full_text returns JSON with 'error' key on DB failure (not plain 'ERROR:')."""
        self._patch_closed(db_conn)
        result = search_full_text("databases")
        data = json.loads(result)
        assert "error" in data, f"Expected JSON with 'error' key, got: {result[:200]}"

    # -- Connection raises tests --

    def test_list_entities_db_raises(self):
        self._patch_raises()
        result = list_entities()
        assert result.startswith("ERROR:"), f"Expected ERROR:, got: {result[:100]}"

    def test_get_entity_profile_db_raises(self):
        self._patch_raises()
        result = get_entity_profile("anna_korhonen")
        assert result.startswith("ERROR:"), f"Expected ERROR:, got: {result[:100]}"

    def test_get_summary_db_raises(self):
        self._patch_raises()
        result = get_summary("day", ["2026-06-15"])
        assert result.startswith("ERROR:"), f"Expected ERROR:, got: {result[:100]}"

    def test_list_summary_files_db_raises(self):
        self._patch_raises()
        result = list_summary_files("day")
        assert result.startswith("ERROR:"), f"Expected ERROR:, got: {result[:100]}"

    def test_list_raw_files_db_raises(self):
        self._patch_raises()
        result = list_raw_files()
        assert result.startswith("ERROR:"), f"Expected ERROR:, got: {result[:100]}"

    def test_get_raw_entry_db_raises(self):
        self._patch_raises()
        result = get_raw_entry(["2026-06-15"])
        assert result.startswith("ERROR:"), f"Expected ERROR:, got: {result[:100]}"

    def test_search_full_text_db_raises(self):
        """search_full_text returns JSON error when _get_db raises."""
        self._patch_raises()
        result = search_full_text("databases")
        data = json.loads(result)
        assert "error" in data, f"Expected JSON with 'error' key, got: {result[:200]}"

    # -- DB-independent tool --

    def test_get_diary_architecture_help_db_resilient(self, db_conn):
        """This tool returns static text regardless of DB state."""
        self._patch_closed(db_conn)
        result = get_diary_architecture_help()
        assert "DIARY DATABASE" in result
        assert not _is_error_response(result)


# ──────────────────────────────────────────────────────────────────────────────
#  U2: Entity Tools
# ──────────────────────────────────────────────────────────────────────────────

class TestListEntities:
    """list_entities() returns sorted entity directory."""

    def test_happy_path(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = list_entities()
        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["id"] == "anna_korhonen"
        assert data[0]["mentions"] == 3
        assert data[1]["id"] == "mikko_virtanen"
        assert data[1]["mentions"] == 0
        for item in data:
            assert "id" in item
            assert "role" in item
            assert "mentions" in item

    def test_empty_db(self, monkeypatch, db_conn):
        _monkeypatch_db(monkeypatch, db_conn)
        result = list_entities()
        assert result == "No entity profiles found."


class TestGetEntityProfile:
    """get_entity_profile() retrieves and filters entity data."""

    def test_happy_path(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_entity_profile("anna_korhonen")
        data = json.loads(result)
        assert "arc_summary" in data
        assert "evidence_log" in data
        assert len(data["evidence_log"]) == 2

    def test_alias_resolution(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_entity_profile("Anna")
        data = json.loads(result)
        assert "arc_summary" in data
        assert len(data["evidence_log"]) == 2

    def test_not_found(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_entity_profile("nonexistent")
        assert "ERROR: Entity ID or alias" in result
        assert "list_entities()" in result

    def test_mode_arc(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_entity_profile("anna_korhonen", mode="arc")
        data = json.loads(result)
        assert "arc_summary" in data
        assert "evidence_log" not in data

    def test_mode_mentions(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_entity_profile("anna_korhonen", mode="mentions")
        data = json.loads(result)
        assert "evidence_log" in data
        assert "arc_summary" not in data

    def test_invalid_mode(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_entity_profile("anna_korhonen", mode="invalid")
        assert "ERROR: Unknown mode" in result

    def test_as_of_date_filter(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_entity_profile("anna_korhonen", as_of_date="2025-01-01")
        data = json.loads(result)
        assert len(data["evidence_log"]) == 1
        assert data["evidence_log"][0]["valid_from"] == "2024-03-01"


# ──────────────────────────────────────────────────────────────────────────────
#  U3: Summary Tools
# ──────────────────────────────────────────────────────────────────────────────

class TestListSummaryFiles:
    """list_summary_files() enumerates summaries at each level."""

    @pytest.mark.parametrize("level,expected_files", [
        ("day", ["2026-06-15"]),
        ("week", ["2026-06-15"]),
        ("month", ["2026-06"]),
        ("year", ["2026"]),
    ])
    def test_happy_path(self, monkeypatch, seeded_db, level, expected_files):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = list_summary_files(level)
        data = json.loads(result)
        assert data["level"] == level
        assert data["total_files"] == len(expected_files)
        assert data["files"] == expected_files

    def test_empty_level(self, monkeypatch, db_conn):
        _monkeypatch_db(monkeypatch, db_conn)
        result = list_summary_files("day")
        assert "No summaries found" in result

    def test_invalid_level(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = list_summary_files("invalid")
        assert result.startswith("ERROR:")


class TestGetSummary:
    """get_summary() retrieves summaries with proper status codes."""

    def test_happy_path(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_summary("day", ["2026-06-15"])
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["status"] == "success"
        assert "data" in data[0]
        assert data[0]["data"]["summary"] == "Productive day working on databases."

    def test_multi_date(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_summary("day", ["2026-06-15", "2099-01-01"])
        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["status"] == "success"
        assert data[1]["status"] == "not_found"

    def test_not_found(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_summary("day", ["2099-01-01"])
        data = json.loads(result)
        assert data[0]["status"] == "not_found"
        assert "smart_fallback_hint" in data[0]

    def test_invalid_level(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_summary("invalid", ["2026-06-15"])
        assert result.startswith("ERROR:")


# ──────────────────────────────────────────────────────────────────────────────
#  U4: Raw Entry Tools
# ──────────────────────────────────────────────────────────────────────────────

class TestListRawFiles:
    """list_raw_files() enumerates available raw entry dates."""

    def test_happy_path(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = list_raw_files()
        data = json.loads(result)
        assert data["total_files"] == 3
        assert data["files"] == ["2026-06-15", "2026-06-16", "2026-06-17"]

    def test_empty_db(self, monkeypatch, db_conn):
        _monkeypatch_db(monkeypatch, db_conn)
        result = list_raw_files()
        assert result == "No raw entries found."


class TestGetRawEntry:
    """get_raw_entry() retrieves raw chat logs with proper status codes."""

    def test_happy_path(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_raw_entry(["2026-06-15"])
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["status"] == "success"
        assert len(data[0]["data"]["entries"]) == 2

    def test_multi_date(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_raw_entry(["2026-06-15", "2099-01-01"])
        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["status"] == "success"
        assert data[1]["status"] == "not_found"

    def test_not_found(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_raw_entry(["2099-01-01"])
        data = json.loads(result)
        assert data[0]["status"] == "not_found"
        assert "smart_fallback_hint" in data[0]

    def test_entry_fields(self, monkeypatch, seeded_db):
        _monkeypatch_db(monkeypatch, seeded_db)
        result = get_raw_entry(["2026-06-15"])
        data = json.loads(result)
        entry = data[0]["data"]["entries"][0]
        assert "time" in entry
        assert "source" in entry
        assert "role" in entry
        assert "text" in entry
        assert "source_file" in entry


# ──────────────────────────────────────────────────────────────────────────────
#  U5: Full Text Search
# ──────────────────────────────────────────────────────────────────────────────

class TestSearchFullText:
    """search_full_text() searches via FTS5 index (all scopes require it)."""

    def _setup_with_fts(self, seeded_db):
        rebuild_fts_index(seeded_db)
        return seeded_db

    def test_user_scope(self, monkeypatch, seeded_db):
        conn = self._setup_with_fts(seeded_db)
        _monkeypatch_db(monkeypatch, conn)
        result = search_full_text("databases", scope="user")
        data = json.loads(result)
        assert data["total_hits"] >= 1
        assert data["results"][0]["matched_word"] == "databases"
        assert data["results"][0]["level"] == "raw"
        assert ">>databases<<" in data["results"][0]["context"]

    def test_raw_scope(self, monkeypatch, seeded_db):
        conn = self._setup_with_fts(seeded_db)
        _monkeypatch_db(monkeypatch, conn)
        result = search_full_text("fun", scope="raw")
        data = json.loads(result)
        assert data["total_hits"] >= 1

    def test_summaries_scope(self, monkeypatch, seeded_db):
        conn = self._setup_with_fts(seeded_db)
        _monkeypatch_db(monkeypatch, conn)
        result = search_full_text("coding", scope="summaries")
        data = json.loads(result)
        assert data["total_hits"] >= 1

    def test_all_scope(self, monkeypatch, seeded_db):
        conn = self._setup_with_fts(seeded_db)
        _monkeypatch_db(monkeypatch, conn)
        result = search_full_text("databases", scope="all")
        data = json.loads(result)
        assert data["total_hits"] >= 1

    def test_day_scope(self, monkeypatch, seeded_db):
        conn = self._setup_with_fts(seeded_db)
        _monkeypatch_db(monkeypatch, conn)
        result = search_full_text("databases", scope="day")
        data = json.loads(result)
        assert "total_hits" in data

    def test_no_results(self, monkeypatch, seeded_db):
        conn = self._setup_with_fts(seeded_db)
        _monkeypatch_db(monkeypatch, conn)
        result = search_full_text("xyznonexistent123")
        data = json.loads(result)
        assert data["total_hits"] == 0

    def test_date_filter(self, monkeypatch, seeded_db):
        conn = self._setup_with_fts(seeded_db)
        _monkeypatch_db(monkeypatch, conn)
        result = search_full_text("day", scope="user", date_from="2026-06-16")
        data = json.loads(result)
        for hit in data["results"]:
            assert hit["date"] >= "2026-06-16"

    def test_empty_query(self, monkeypatch, seeded_db):
        conn = self._setup_with_fts(seeded_db)
        _monkeypatch_db(monkeypatch, conn)
        result = search_full_text("")
        data = json.loads(result)
        assert data["total_hits"] == 0

    def test_user_scope_without_fts_index(self, monkeypatch, seeded_db):
        """scope='user' uses LIKE (not MATCH), so it works without FTS index."""
        _monkeypatch_db(monkeypatch, seeded_db)
        result = search_full_text("databases", scope="user")
        data = json.loads(result)
        assert "total_hits" in data
        assert data["total_hits"] >= 1

    def test_empty_fts_returns_zero_hits(self, monkeypatch, seeded_db):
        """Without rebuilding FTS index, content_fts is empty so search returns 0 hits."""
        _monkeypatch_db(monkeypatch, seeded_db)
        result = search_full_text("coding", scope="summaries")
        data = json.loads(result)
        assert data["total_hits"] == 0


# ──────────────────────────────────────────────────────────────────────────────
#  U6: Helper Function —  get_closest_files
# ──────────────────────────────────────────────────────────────────────────────

class TestGetClosestFiles:
    """get_closest_files() navigates to nearest available periods."""

    def test_both_sides(self):
        prev_f, next_f = get_closest_files("2026-06-15", ["2026-06-10", "2026-06-20"])
        assert prev_f == "2026-06-10"
        assert next_f == "2026-06-20"

    def test_only_previous(self):
        prev_f, next_f = get_closest_files("2026-06-25", ["2026-06-10", "2026-06-20"])
        assert prev_f == "2026-06-20"
        assert next_f is None

    def test_only_next(self):
        prev_f, next_f = get_closest_files("2026-06-05", ["2026-06-10", "2026-06-20"])
        assert prev_f is None
        assert next_f == "2026-06-10"

    def test_empty_list(self):
        prev_f, next_f = get_closest_files("any", [])
        assert prev_f is None
        assert next_f is None

    def test_single_available(self):
        prev_f, next_f = get_closest_files("2026-06-15", ["2026-06-10"])
        assert prev_f == "2026-06-10"
        assert next_f is None
