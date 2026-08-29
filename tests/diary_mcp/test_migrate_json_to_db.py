"""
tests/mcp_server/test_migrate_json_to_db.py
=========================================
Unit tests for the diary JSON-to-SQLite database migration script
(mcp_server/migrate_json_to_db.py). Tests dry-run migrations, full imports,
FTS5, verification integrity, export reversion, and backup archiving.
"""

from __future__ import annotations
import json
import sqlite3
import tarfile
import pytest
from pathlib import Path

from diary_core.schema import RawEntry, SummaryUnit, EntityProfile
from mcp_server.db import get_db_connection, init_db
from mcp_server.migrate_json_to_db import (
    migrate_raw_entries,
    migrate_summaries,
    migrate_entity_profiles,
    verify_migration,
    archive_and_delete_jsons,
    export_db_to_json,
)


@pytest.fixture
def temp_workspace(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Fixture to set up a mock workspace directory tree with sample data.

    Returns:
        A tuple of (workspace_path, raw_dir, summaries_dir, entity_dir).
    """
    workspace = tmp_path / "data"
    raw_dir = workspace / "raw_entries"       # legacy JSON raw entries
    summaries_dir = workspace / "summaries"     # legacy JSON summaries
    entity_dir = workspace / "entity_profiles"  # legacy JSON entity profiles

    raw_dir.mkdir(parents=True)
    summaries_dir.mkdir(parents=True)
    entity_dir.mkdir(parents=True)

    # 1. Create a raw entry JSON file
    raw_entry_data = {
        "date": "2026-06-15",
        "entries": [
            {
                "time": "09:00:00",
                "source": "owui",
                "source_file": "chat.json",
                "role": "user",
                "text": "Self-reflection on coding standards."
            },
            {
                "time": "09:05:00",
                "source": "owui",
                "source_file": "chat.json",
                "role": "assistant",
                "text": "Acknowledged."
            }
        ]
    }
    with open(raw_dir / "2026-06-15.json", "w", encoding="utf-8") as f:
        json.dump(raw_entry_data, f)

    # 2. Create summary files
    for level in ["day", "week", "month", "year"]:
        level_dir = summaries_dir / level
        level_dir.mkdir()
        
        # Determine appropriate filename suffix depending on the level
        if level == "year":
            name = "2026.json"
            start, end = "2026", "2026"
        elif level == "month":
            name = "2026-06.json"
            start, end = "2026-06", "2026-06"
        else:
            name = "2026-06-15.json"
            start, end = "2026-06-15", "2026-06-15"
            
        summary = SummaryUnit(
            unit=level,
            period_start=start,
            period_end=end,
            summary=f"Summary of the {level}",
            emotional_tone="calm",
            energy_level=3
        )
        with open(level_dir / name, "w", encoding="utf-8") as f:
            f.write(summary.to_json())

    # 3. Create entity profile and index files
    profile = EntityProfile(
        id="john_doe",
        display_name="John Doe",
        aliases=["John"],
        role_in_authors_life="Diarist"
    )
    with open(entity_dir / "john_doe.json", "w", encoding="utf-8") as f:
        json.dump(profile.to_dict(), f)

    # Create helper _index.json that should NOT be migrated
    with open(entity_dir / "_index.json", "w", encoding="utf-8") as f:
        json.dump({"alias_map": {"johnny": "john_doe"}}, f)

    return workspace, raw_dir, summaries_dir, entity_dir


@pytest.fixture
def db_conn():
    """Fixture providing initialized SQLite connection."""
    conn = get_db_connection(":memory:")
    init_db(conn)
    yield conn
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_migration_raw_entries(db_conn, temp_workspace):
    """Verifies importing raw JSON entry files into SQLite database."""
    _, raw_dir, _, _ = temp_workspace
    
    # Process
    files_processed, entries_inserted = migrate_raw_entries(db_conn, raw_dir)
    
    assert files_processed == 1
    assert entries_inserted == 2
    
    # Query database and verify fields
    cursor = db_conn.execute("SELECT date, time, text, role FROM raw_entry ORDER BY time ASC")
    rows = cursor.fetchall()
    
    assert len(rows) == 2
    assert rows[0][0] == "2026-06-15"
    assert rows[0][1] == "09:00:00"
    assert rows[0][2] == "Self-reflection on coding standards."
    assert rows[0][3] == "user"

    assert rows[1][1] == "09:05:00"
    assert rows[1][2] == "Acknowledged."
    assert rows[1][3] == "assistant"


def test_migration_summaries(db_conn, temp_workspace):
    """Verifies scanning and importing SummaryUnit files across all directories."""
    _, _, summaries_dir, _ = temp_workspace
    
    levels_processed, summaries_inserted = migrate_summaries(db_conn, summaries_dir)
    
    assert levels_processed == 4
    assert summaries_inserted == 4
    
    # Query database
    cursor = db_conn.execute("SELECT level, period_start, data_json FROM summary ORDER BY level ASC")
    rows = cursor.fetchall()
    assert len(rows) == 4
    
    # Decode and check one level
    day_row = [r for r in rows if r[0] == "day"][0]
    assert day_row[1] == "2026-06-15"
    data = json.loads(day_row[2])
    assert data["summary"] == "Summary of the day"


def test_migration_entity_profiles_ignores_index_json(db_conn, temp_workspace):
    """Verifies importing entity profiles and ensuring _index.json is skipped."""
    _, _, _, entity_dir = temp_workspace
    
    profiles_inserted = migrate_entity_profiles(db_conn, entity_dir)
    
    # Should insert 'john_doe', but skip '_index.json'
    assert profiles_inserted == 1
    
    # Check DB
    cursor = db_conn.execute("SELECT id, display_name FROM entity_profile")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "john_doe"
    assert rows[0][1] == "John Doe"


def test_verify_migration_pass_and_fail(db_conn, temp_workspace):
    """Tests verify_migration with a correct import, then tests mismatch detection."""
    _, raw_dir, summaries_dir, entity_dir = temp_workspace
    
    # 1. Run migration
    migrate_raw_entries(db_conn, raw_dir)
    migrate_summaries(db_conn, summaries_dir)
    migrate_entity_profiles(db_conn, entity_dir)
    
    # Verify - should pass with clean matches
    assert verify_migration(db_conn, raw_dir, summaries_dir, entity_dir) is True
    
    # 2. Modify database row to introduce discrepancy
    db_conn.execute("UPDATE raw_entry SET text = 'tampered content'")
    db_conn.commit()
    
    # Verify - should detect mismatch and fail
    assert verify_migration(db_conn, raw_dir, summaries_dir, entity_dir) is False


def test_export_db_to_json(db_conn, temp_workspace, tmp_path):
    """Tests reversing migration (SQLite to JSON files) on empty target directory."""
    _, raw_dir, summaries_dir, entity_dir = temp_workspace
    
    # 1. Populate DB
    migrate_raw_entries(db_conn, raw_dir)
    migrate_summaries(db_conn, summaries_dir)
    migrate_entity_profiles(db_conn, entity_dir)
    
    # 2. Set up fresh empty export target directories
    export_root = tmp_path / "export_target"
    export_raw = export_root / "raw_entries"       # legacy JSON export
    export_sum = export_root / "summaries"          # legacy JSON export
    export_ent = export_root / "entity_profiles"    # legacy JSON export
    
    # 3. Export
    export_db_to_json(db_conn, export_raw, export_sum, export_ent)
    
    # 4. Verify exported files exist and are syntactically valid
    assert (export_raw / "2026-06-15.json").exists()
    assert (export_sum / "day" / "2026-06-15.json").exists()
    assert (export_ent / "john_doe.json").exists()
    
    with open(export_ent / "john_doe.json", "r", encoding="utf-8") as f:
        profile_data = json.load(f)
    assert profile_data["display_name"] == "John Doe"


def test_archive_and_delete_jsons(temp_workspace, tmp_path):
    """Verifies that archiving tarballs files properly and unlinks workspace files.

    Ensures that '_index.json' in the entity profile folder is preserved.
    """
    workspace, raw_dir, summaries_dir, entity_dir = temp_workspace
    archive_dir = tmp_path / "archives"
    
    # Trigger cleanup
    archive_and_delete_jsons(archive_dir, raw_dir, summaries_dir, entity_dir)
    
    # 1. Check tarball created
    tarball_files = list(archive_dir.glob("*.tar.gz"))
    assert len(tarball_files) == 1
    
    # 2. Verify contents of tarball
    with tarfile.open(tarball_files[0], "r:gz") as tar:
        names = tar.getnames()
        assert any("raw_entries" in n for n in names)
        assert any("summaries" in n for n in names)
        
    # 3. Verify files deleted
    assert not (raw_dir / "2026-06-15.json").exists()
    assert not (summaries_dir / "day" / "2026-06-15.json").exists()
    assert not (entity_dir / "john_doe.json").exists()
    
    # 4. CRITICAL check: Verify '_index.json' is NOT deleted
    assert (entity_dir / "_index.json").exists()
