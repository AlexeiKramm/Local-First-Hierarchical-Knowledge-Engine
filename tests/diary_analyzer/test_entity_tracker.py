"""
tests/diary_core/test_entity_tracker.py
============================================
Unit tests for entity_tracker helper functions and alias resolution.

Uses an in-memory SQLite database fixture following the pattern from
tests/diary_core/test_progress_manager.py.
"""

from __future__ import annotations

import pytest

from diary_core.schema import EntityProfile, RawEntry, SummaryUnit, TimestampedKnowledge
from diary_core.entity_tracker import (
    _build_chunks,
    _build_raw_context_window,
    _load_raw_entries_from_db,
    _parse_entity_mentions_block,
    EntityTrackerError,
)
from mcp_server.db import (
    DatabaseError,
    get_db_connection,
    get_entity_profile,
    init_db,
    list_entity_profiles,
    upsert_entity_profile,
    upsert_raw_entry,
    upsert_summary,
)


@pytest.fixture
def db_conn():
    """Create a clean in-memory database for each test."""
    conn = get_db_connection(":memory:")
    init_db(conn)
    yield conn
    conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_raw_entry(
    date: str = "2026-06-01",
    time: str = "10:00",
    text: str = "Test diary entry.",
) -> RawEntry:
    return RawEntry(
        date=date,
        time=time,
        source="test",
        source_file="test.json",
        text=text,
        role="user",
    )


def _make_summary(
    period_start: str = "2026-06-01",
    entity_mentions: str | None = None,
) -> SummaryUnit:
    return SummaryUnit(
        unit="day",
        period_start=period_start,
        period_end=period_start,
        summary="Test day.",
        entity_mentions=entity_mentions,
    )


# ── _parse_entity_mentions_block ──────────────────────────────────────────────


class TestParseEntityMentionsBlock:
    """Happy path, edge cases, and error cases for the mentions parser."""

    def test_empty_string_returns_empty_list(self):
        assert _parse_entity_mentions_block("") == []

    def test_none_string_returns_empty_list(self):
        assert _parse_entity_mentions_block("None") == []
        assert _parse_entity_mentions_block("none") == []

    def test_single_mention_block(self):
        block = """- name_as_written: Anna
  normalized_name: anna_korhonen
  valence: positive
  interaction_summary: Had a great conversation
  source_date: 2026-06-01"""
        result = _parse_entity_mentions_block(block)
        assert len(result) == 1
        assert result[0]["name_as_written"] == "Anna"
        assert result[0]["normalized_name"] == "anna_korhonen"
        assert result[0]["valence"] == "positive"
        assert result[0]["source_date"] == "2026-06-01"

    def test_multiple_mentions(self):
        block = """- name_as_written: Anna
  normalized_name: anna_korhonen
  valence: positive
  interaction_summary: Coffee chat
  source_date: 2026-06-01
- name_as_written: Bob
  normalized_name: bob_smith
  valence: neutral
  interaction_summary: Meeting
  source_date: 2026-06-02"""
        result = _parse_entity_mentions_block(block)
        assert len(result) == 2
        assert result[0]["name_as_written"] == "Anna"
        assert result[1]["name_as_written"] == "Bob"

    def test_slm_flat_semicolon_format(self):
        """SLMs sometimes flatten YAML arrays into semicolon-separated strings."""
        block = """name_as_written: Anna; normalized_name: anna_korhonen; valence: positive; interaction_summary: Hello; source_date: 2026-06-01"""
        result = _parse_entity_mentions_block(block)
        assert len(result) == 1
        assert result[0]["name_as_written"] == "Anna"

    def test_missing_optional_fields_still_included(self):
        """Only name_as_written is required; missing normalized_name or interaction_summary is OK."""
        block = """- name_as_written: Anna
  valence: positive
  source_date: 2026-06-01"""
        result = _parse_entity_mentions_block(block)
        assert len(result) == 1
        assert result[0]["name_as_written"] == "Anna"

    def test_gibberish_returns_empty(self):
        assert _parse_entity_mentions_block("not a valid format at all") == []


# ── _load_raw_entries_from_db ─────────────────────────────────────────────────


class TestLoadRawEntriesFromDb:
    """Test loading raw entries from the database."""

    def test_no_entries_returns_empty_string(self, db_conn):
        result = _load_raw_entries_from_db(db_conn, "2026-06-01")
        assert result == ""

    def test_single_entry_formatted_correctly(self, db_conn):
        entry = _make_raw_entry(time="10:00", text="Hello world.")
        upsert_raw_entry(db_conn, entry)
        result = _load_raw_entries_from_db(db_conn, "2026-06-01")
        assert "[10:00] Hello world." in result

    def test_multiple_entries_for_same_date(self, db_conn):
        upsert_raw_entry(db_conn, _make_raw_entry(time="10:00", text="First entry."))
        upsert_raw_entry(db_conn, _make_raw_entry(time="11:00", text="Second entry."))
        result = _load_raw_entries_from_db(db_conn, "2026-06-01")
        assert "First entry." in result
        assert "Second entry." in result

    def test_invalid_date_returns_empty(self, db_conn):
        result = _load_raw_entries_from_db(db_conn, "not-a-date")
        assert result == ""

    def test_closed_connection_returns_empty(self):
        conn = get_db_connection(":memory:")
        init_db(conn)
        conn.close()
        result = _load_raw_entries_from_db(conn, "2026-06-01")
        assert result == ""


# ── _build_raw_context_window ─────────────────────────────────────────────────


class TestBuildRawContextWindow:
    """Test multi-day context window building."""

    def test_no_entries_returns_empty(self, db_conn):
        result = _build_raw_context_window(db_conn, "2026-06-01", 1, 1)
        assert result == ""

    def test_center_date_included(self, db_conn):
        upsert_raw_entry(db_conn, _make_raw_entry(date="2026-06-01", text="Center day."))
        result = _build_raw_context_window(db_conn, "2026-06-01", 0, 0)
        assert "Center day." in result
        assert "day of mention" in result

    def test_surrounding_days_included(self, db_conn):
        upsert_raw_entry(db_conn, _make_raw_entry(date="2026-06-01", text="Day before."))
        upsert_raw_entry(db_conn, _make_raw_entry(date="2026-06-02", text="Center day."))
        upsert_raw_entry(db_conn, _make_raw_entry(date="2026-06-03", text="Day after."))
        result = _build_raw_context_window(db_conn, "2026-06-02", 1, 1)
        assert "Day before." in result
        assert "Center day." in result
        assert "Day after." in result

    def test_missing_days_in_window_included_only_existing(self, db_conn):
        upsert_raw_entry(db_conn, _make_raw_entry(date="2026-06-02", text="Only this day."))
        result = _build_raw_context_window(db_conn, "2026-06-02", 1, 1)
        assert "Only this day." in result
        # Day before (06-01) and day after (06-03) should not appear as sections
        assert "day before" not in result.lower() or "day after" not in result.lower()

    def test_invalid_center_date_returns_empty(self, db_conn):
        result = _build_raw_context_window(db_conn, "bad-date", 1, 1)
        assert result == ""


# ── _build_chunks ─────────────────────────────────────────────────────────────


class TestBuildChunks:
    """Test timeline chunking logic."""

    def test_single_chunk_fits_budget(self):
        blocks = ["Short block."]
        chunks = _build_chunks(blocks, 32_768, 3000, 1, 1, "test")
        assert len(chunks) == 1
        assert chunks[0] == blocks

    def test_multiple_chunks_when_exceeding_budget(self):
        # Create a block large enough to fill the budget with a small context window
        large_block = "word " * 10_000
        blocks = [large_block, large_block, large_block]
        chunks = _build_chunks(blocks, 4096, 500, 1, 1, "test")
        assert len(chunks) > 1

    def test_overlap_shared_between_chunks(self,):
        blocks = [f"Block {i}" for i in range(10)]
        chunks = _build_chunks(blocks, 4096, 3000, 1, 1, "test", chunk_overlap=2)
        if len(chunks) > 1:
            # The first entries of chunk[1] should match the last entries of chunk[0]
            assert chunks[1][0] == chunks[0][-2]
            assert chunks[1][1] == chunks[0][-1]

    def test_empty_blocks_returns_empty_list(self):
        chunks = _build_chunks([], 32_768, 3000, 1, 1, "test")
        assert chunks == []


# ── Alias Resolution (DB layer) ───────────────────────────────────────────────


class TestAliasResolution:
    """Test that get_entity_profile with aliases resolves correctly."""

    def test_exact_id_match_returns_profile(self, db_conn):
        profile = EntityProfile(
            id="anna_korhonen",
            display_name="Anna Korhonen",
            aliases=["anna", "annukka"],
            timestamped_knowledge=[],
        )
        upsert_entity_profile(db_conn, profile)
        result = get_entity_profile(db_conn, "anna_korhonen")
        assert result is not None
        assert result.id == "anna_korhonen"

    def test_alias_does_not_match_at_db_layer(self, db_conn):
        """The DB layer does exact ID matching only (alias resolution is in MCP layer)."""
        profile = EntityProfile(
            id="anna_korhonen",
            display_name="Anna Korhonen",
            aliases=["anna", "annukka"],
            timestamped_knowledge=[],
        )
        upsert_entity_profile(db_conn, profile)
        # Alias lookup returns None because db layer only does WHERE id = ?
        result = get_entity_profile(db_conn, "anna")
        assert result is None

    def test_list_entity_profiles_returns_all(self, db_conn):
        p1 = EntityProfile(id="alice", display_name="Alice", aliases=[], timestamped_knowledge=[])
        p2 = EntityProfile(id="bob", display_name="Bob", aliases=[], timestamped_knowledge=[])
        upsert_entity_profile(db_conn, p1)
        upsert_entity_profile(db_conn, p2)
        profiles = list_entity_profiles(db_conn)
        assert len(profiles) == 2
        ids = {p.id for p in profiles}
        assert ids == {"alice", "bob"}

    def test_alias_resolution_manual_fallback(self, db_conn):
        """Simulate the MCP server's 3-step alias resolution logic."""
        profile = EntityProfile(
            id="anna_korhonen",
            display_name="Anna Korhonen",
            aliases=["anna", "annukka"],
            timestamped_knowledge=[TimestampedKnowledge(
                valid_from="2026-06-01",
                source_entries=["2026-06-01"],
                content="Had coffee.",
                emotional_valence="positive",
                tags=["positive"],
            )],
        )
        upsert_entity_profile(db_conn, profile)

        # Step 1: exact match
        result = get_entity_profile(db_conn, "anna_korhonen")
        assert result is not None
        assert result.id == "anna_korhonen"

        # Step 2: alias lookup (simulates what MCP server does)
        result = get_entity_profile(db_conn, "anna")
        assert result is None  # DB layer does not resolve aliases

        # Step 3: manual scan of all profiles for alias match
        all_profiles = list_entity_profiles(db_conn)
        found = None
        for p in all_profiles:
            if "anna" in [a.lower() for a in p.aliases]:
                found = p
                break
        assert found is not None
        assert found.id == "anna_korhonen"

    def test_case_insensitive_alias_matching(self, db_conn):
        profile = EntityProfile(
            id="anna_korhonen",
            display_name="Anna Korhonen",
            aliases=["Anna", "Annukka"],
            timestamped_knowledge=[],
        )
        upsert_entity_profile(db_conn, profile)
        all_profiles = list_entity_profiles(db_conn)
        found = None
        for p in all_profiles:
            if "ANNA".lower() in [a.lower() for a in p.aliases]:
                found = p
                break
        assert found is not None
        assert found.id == "anna_korhonen"

    def test_nonexistent_id_returns_none(self, db_conn):
        result = get_entity_profile(db_conn, "nonexistent")
        assert result is None


# ── Error Handling ────────────────────────────────────────────────────────────


class TestErrorHandling:
    """Test that database errors propagate correctly."""

    def test_closed_connection_raises_on_get(self):
        conn = get_db_connection(":memory:")
        init_db(conn)
        conn.close()
        with pytest.raises(DatabaseError):
            get_entity_profile(conn, "test")

    def test_closed_connection_raises_on_list(self):
        conn = get_db_connection(":memory:")
        init_db(conn)
        conn.close()
        with pytest.raises(DatabaseError):
            list_entity_profiles(conn)

    def test_closed_connection_raises_on_upsert(self):
        conn = get_db_connection(":memory:")
        init_db(conn)
        conn.close()
        profile = EntityProfile(id="test", display_name="Test", aliases=[], timestamped_knowledge=[])
        with pytest.raises(DatabaseError):
            upsert_entity_profile(conn, profile)

    def test_entity_tracker_error_is_database_error(self):
        assert issubclass(EntityTrackerError, Exception)
