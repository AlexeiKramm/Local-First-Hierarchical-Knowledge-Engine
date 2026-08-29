"""
tests/diary_ingestor/test_assembler.py
======================================
Unit tests for the diary ingestor's Assembler (diary_ingestor/assembler.py).
Tests path resolution, database assembly/deduplication, and legacy JSON export.
"""

from __future__ import annotations
import json
import pytest
from pathlib import Path

from diary_ingestor.assembler import Assembler
from mcp_server.db import get_db_connection, DatabaseError


@pytest.fixture
def mock_db_path(tmp_path: Path) -> Path:
    """Fixture providing a clean path for a temporary database file."""
    return tmp_path / "diary.db"


def test_assembler_path_resolution(tmp_path):
    """Happy Path: Verifies direct database file path resolution."""
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True)
    
    # 1. Direct database file resolution
    db_file = db_dir / "diary.db"
    assembler_file = Assembler(db_file)
    assert assembler_file.db_path == db_file
    
    # 2. Legacy 'raw_entries' directory compatibility (resolve to parent/diary.db)
    raw_dir = tmp_path / "raw_entries"
    raw_dir.mkdir()
    assembler_dir = Assembler(raw_dir)
    assert assembler_dir.db_path == tmp_path / "diary.db"


def test_assemble_happy_path(mock_db_path):
    """Happy Path: Tests importing fresh entries into an empty database."""
    staged = [
        {
            "date": "2026-06-15",
            "time": "09:00:00",
            "source": "gemini",
            "source_file": "activity.html",
            "role": "user",
            "text": "First staged entry."
        },
        {
            "date": "2026-06-15",
            "time": "09:05:00",
            "source": "gemini",
            "source_file": "activity.html",
            "role": "assistant",
            "text": "Second staged entry."
        },
        {
            "date": "2026-06-16",
            "time": "12:00:00",
            "source": "owui",
            "source_file": "chat.json",
            "role": "user",
            "text": "Another day's entry."
        }
    ]

    assembler = Assembler(mock_db_path)
    result = assembler.assemble(staged)

    # 1. Assert counts in return dictionary
    assert result["new_dates"] == 2
    assert result["appended"] == 3
    assert result["skipped_duplicates"] == 0

    # 2. Query database directly to verify contents
    conn = get_db_connection(mock_db_path)
    cursor = conn.execute("SELECT date, time, text, role FROM raw_entry ORDER BY date ASC, time ASC")
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 3
    assert rows[0][0] == "2026-06-15"
    assert rows[0][1] == "09:00:00"
    assert rows[0][2] == "First staged entry."
    assert rows[0][3] == "user"

    assert rows[2][0] == "2026-06-16"
    assert rows[2][1] == "12:00:00"
    assert rows[2][2] == "Another day's entry."
    assert rows[2][3] == "user"


def test_assemble_duplicate_handling(mock_db_path):
    """Edge Case: Tests duplicate skipping when trying to import identical entries."""
    staged = [
        {
            "date": "2026-06-15",
            "time": "09:00:00",
            "source": "gemini",
            "source_file": "activity.html",
            "role": "user",
            "text": "First staged entry."
        }
    ]

    assembler = Assembler(mock_db_path)
    
    # First assembly
    res_1 = assembler.assemble(staged)
    assert res_1["appended"] == 1
    assert res_1["skipped_duplicates"] == 0

    # Second assembly (identical items)
    res_2 = assembler.assemble(staged)
    assert res_2["appended"] == 0
    assert res_2["skipped_duplicates"] == 1
    assert res_2["new_dates"] == 0


def test_regenerate_merged(mock_db_path, tmp_path):
    """Happy Path: Verifies exporting SQLite records back to a monolithic JSON file."""
    staged = [
        {
            "date": "2026-06-15",
            "time": "09:00:00",
            "source": "gemini",
            "source_file": "activity.html",
            "role": "user",
            "text": "Entry text."
        }
    ]

    assembler = Assembler(mock_db_path)
    assembler.assemble(staged)
    
    export_path = tmp_path / "merged_diary.json"
    written = assembler.regenerate_merged(export_path)
    
    assert written == 1
    assert export_path.exists()
    
    # Parse exported file structure
    with open(export_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    assert "metadata" in data
    assert "entries" in data
    assert data["metadata"]["total_entries"] == 1
    assert data["entries"][0]["date"] == "2026-06-15"
    assert data["entries"][0]["entries"][0]["text"] == "Entry text."


def test_assemble_error_handling(tmp_path):
    """Error Case: Verifies that database operations propagate DatabaseError on failures."""
    # Target a path with invalid characters that will fail folder creation on Windows
    invalid_db_path = tmp_path / "invalid_dir_???" / "diary.db"
    
    assembler = Assembler(invalid_db_path)
    staged = [{"date": "2026-06-15", "text": "Will fail"}]
    
    with pytest.raises(DatabaseError):
        assembler.assemble(staged)
        
    with pytest.raises(DatabaseError):
        assembler.regenerate_merged(tmp_path / "test.json")
