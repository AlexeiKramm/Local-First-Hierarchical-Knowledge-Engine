"""
input_loader.py
===============
Loads the unified merged JSON (produced by raw_data_merger.py) and
exposes it as a sorted list of DayEntries objects.

Also supports loading individual raw formats directly (txt, md, csv, json)
for users who want to skip the merger step.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from mcp_server.db import DatabaseError, get_raw_entries_by_date
from .schema import DayEntries, RawEntry


# ─────────────────────────────────────────────
#  Primary loader — merged JSON
# ─────────────────────────────────────────────

def load_merged_json(
    filepath: str,
    roles: list[str] | None = None,
) -> list[DayEntries]:
    """
    Load the unified JSON produced by raw_data_merger.py.

    Args:
        filepath: path to the merged JSON file.
        roles:    optional role filter — e.g. ["user"], ["assistant"],
                  or ["user", "assistant"]. None (default) keeps all entries.

    Expected structure:
      {
        "metadata": {...},
        "entries": [
          {
            "date": "YYYY-MM-DD",
            "entries": [
              {"time": "...", "source": "...", "source_file": "...",
               "role": "user", "text": "..."},
              ...
            ]
          },
          ...
        ]
      }
    """
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    days: list[DayEntries] = []
    for day_block in data.get("entries", []):
        date = day_block.get("date", "")
        raw_entries: list[RawEntry] = []
        for e in day_block.get("entries", []):
            text = e.get("text", "").strip()
            if not text:
                continue
            entry_role = e.get("role", "user")   # backward compat: default to "user"
            if roles is not None and entry_role not in roles:
                continue
            raw_entries.append(RawEntry(
                date=date,
                time=e.get("time", "00:00:00"),
                source=e.get("source", "unknown"),
                source_file=e.get("source_file", ""),
                text=text,
                role=entry_role,
            ))
        if raw_entries:
            days.append(DayEntries(date=date, entries=raw_entries))

    days.sort(key=lambda d: d.date)
    return days


def load_split_folder(
    folder: str,
    roles: list[str] | None = None,
) -> list[DayEntries]:
    """
    Load a folder of per-day JSON files (YYYY-MM-DD.json) as produced by
    raw_data_splitter.py or saved by session_manager.py.

    Args:
        folder: path to the directory of day JSON files.
        roles:  optional role filter — same as load_merged_json.
    """
    days: list[DayEntries] = []
    for path in sorted(Path(folder).glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                day_block = json.load(f)
        except Exception:
            continue
        date = day_block.get("date", path.stem)
        raw_entries: list[RawEntry] = []
        for e in day_block.get("entries", []):
            text = e.get("text", "").strip()
            if not text:
                continue
            entry_role = e.get("role", "user")
            if roles is not None and entry_role not in roles:
                continue
            raw_entries.append(RawEntry(
                date=date,
                time=e.get("time", "00:00:00"),
                source=e.get("source", "unknown"),
                source_file=e.get("source_file", path.name),
                text=text,
                role=entry_role,
            ))
        if raw_entries:
            days.append(DayEntries(date=date, entries=raw_entries))

    days.sort(key=lambda d: d.date)
    return days


# ─────────────────────────────────────────────
#  Secondary loaders — individual raw formats
# ─────────────────────────────────────────────

def load_txt_folder(folder: str, date_pattern: str = "%Y-%m-%d") -> list[DayEntries]:
    """
    Load a folder of .txt files where the filename encodes the date.
    Example filename: 2025-03-17.txt
    """
    days: list[DayEntries] = []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".txt"):
            continue
        stem = Path(name).stem
        try:
            dt = datetime.strptime(stem, date_pattern)
        except ValueError:
            continue
        filepath = os.path.join(folder, name)
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            entry = RawEntry(
                date=dt.strftime("%Y-%m-%d"),
                time="00:00:00",
                source="txt",
                source_file=name,
                text=text,
            )
            days.append(DayEntries(date=entry.date, entries=[entry]))
    return days


def load_md_folder(folder: str, date_pattern: str = "%Y-%m-%d") -> list[DayEntries]:
    """
    Load a folder of .md files where the filename encodes the date.
    """
    days: list[DayEntries] = []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".md"):
            continue
        stem = Path(name).stem
        try:
            dt = datetime.strptime(stem, date_pattern)
        except ValueError:
            continue
        filepath = os.path.join(folder, name)
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            entry = RawEntry(
                date=dt.strftime("%Y-%m-%d"),
                time="00:00:00",
                source="md",
                source_file=name,
                text=text,
            )
            days.append(DayEntries(date=entry.date, entries=[entry]))
    return days


def load_csv(
    filepath: str,
    date_col: str = "date",
    text_col: str = "text",
    date_format: str = "%Y-%m-%d",
) -> list[DayEntries]:
    """
    Load a CSV file with a date column and a text column.
    Multiple rows with the same date are merged into one DayEntries.
    """
    from collections import defaultdict

    buckets: dict[str, list[RawEntry]] = defaultdict(list)
    with open(filepath, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_date = row.get(date_col, "").strip()
            text = row.get(text_col, "").strip()
            if not raw_date or not text:
                continue
            try:
                dt = datetime.strptime(raw_date, date_format)
                date_str = dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
            buckets[date_str].append(
                RawEntry(date=date_str, time="00:00:00", source="csv",
                         source_file=Path(filepath).name, text=text)
            )
    return [DayEntries(date=k, entries=v) for k, v in sorted(buckets.items())]


# ─────────────────────────────────────────────
#  Database loader
# ─────────────────────────────────────────────

def load_from_db(
    db_conn: sqlite3.Connection,
    roles: list[str] | None = None,
) -> list[DayEntries]:
    """Load all raw entries from the SQLite database into DayEntries objects.

    Queries all distinct dates from the raw_entry table and fetches entries
    for each date, applying an optional role filter.

    Args:
        db_conn: An open SQLite connection to the diary database.
        roles: Optional list of roles to keep (e.g. ["user"], ["assistant"]).
            None (default) keeps all entries.

    Returns:
        A list of DayEntries sorted by date, each containing its RawEntries
        ordered by time.

    Raises:
        DatabaseError: If the database query fails.
    """
    try:
        cursor = db_conn.execute(
            "SELECT DISTINCT date FROM raw_entry ORDER BY date ASC"
        )
        dates = [row[0] for row in cursor.fetchall()]
    except sqlite3.Error as e:
        raise DatabaseError(f"Failed to query dates from raw_entry: {e}") from e

    days: list[DayEntries] = []
    for date_str in dates:
        raw_entries = get_raw_entries_by_date(db_conn, date_str)
        if roles is not None:
            raw_entries = [e for e in raw_entries if e.role in roles]
        if raw_entries:
            days.append(DayEntries(date=date_str, entries=raw_entries))

    return days


# ─────────────────────────────────────────────
#  Date range filter
# ─────────────────────────────────────────────

def filter_by_date_range(
    days: list[DayEntries],
    start: str | None = None,
    end: str | None = None,
) -> list[DayEntries]:
    """
    Filter a list of DayEntries to only those within [start, end] inclusive.
    start / end are "YYYY-MM-DD" strings. None means no bound.
    """
    result = days
    if start:
        result = [d for d in result if d.date >= start]
    if end:
        result = [d for d in result if d.date <= end]
    return result


# ─────────────────────────────────────────────
#  Week/month grouping helpers
# ─────────────────────────────────────────────

def group_into_weeks(days: list[DayEntries]) -> list[list[DayEntries]]:
    """
    Partition days into ISO calendar weeks.
    Returns a list of lists (each inner list = one week of DayEntries).
    """
    from itertools import groupby

    def iso_week_key(d: DayEntries) -> str:
        dt = datetime.strptime(d.date, "%Y-%m-%d")
        year, week, _ = dt.isocalendar()
        return f"{year}-W{week:02d}"

    return [list(g) for _, g in groupby(days, key=iso_week_key)]


def group_into_months(days: list[DayEntries]) -> list[list[DayEntries]]:
    """
    Partition days into calendar months (YYYY-MM).
    """
    from itertools import groupby

    def month_key(d: DayEntries) -> str:
        return d.date[:7]   # "YYYY-MM"

    return [list(g) for _, g in groupby(days, key=month_key)]
