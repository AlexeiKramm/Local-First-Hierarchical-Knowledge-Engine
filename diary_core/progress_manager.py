"""
progress_manager.py
===================
Tracks and persists summarization progress using a SQLite database.

Design:
  - Each completed SummaryUnit is saved to the 'summary' table via
    mcp_server.db helpers.
  - The database is the sole source of truth — no .json files or manifest.
  - On resume, is_done() queries the DB to skip already-completed units.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from diary_core.schema import SummaryUnit
from mcp_server.db import (
    DatabaseError,
    get_summary,
    list_summaries,
    upsert_summary,
)

# Re-raise DatabaseError as ProgressError for callers that catch locally
class ProgressError(DatabaseError):
    """Raised when a ProgressManager operation fails."""
    pass


class ProgressManager:
    """
    Manages summary persistence via a SQLite database.

    All completed summaries are stored in the 'summary' table and keyed by
    (level, period_start). No manifest or per-unit JSON files are written.

    The caller owns the sqlite3.Connection lifecycle. This class is not
    responsible for opening or closing the connection.
    """

    def __init__(self, db_conn: sqlite3.Connection):
        self._conn = db_conn

    # ── Save / Load ────────────────────────────────────────────────────────

    def save_unit(self, summary: SummaryUnit) -> Path:
        """
        Persist a SummaryUnit to the database and mark it complete.

        Args:
            summary: The SummaryUnit to save.

        Returns:
            A Path object for use in log messages
            (constructs a descriptive path from unit and period_start).

        Raises:
            ProgressError: If the database write fails.
        """
        try:
            upsert_summary(self._conn, summary)
        except DatabaseError as e:
            raise ProgressError(str(e)) from e
        return Path(summary.unit) / summary.period_start

    def load_unit(self, unit: str, key: str) -> Optional[SummaryUnit]:
        """Load a previously saved summary unit.

        Args:
            unit: The summary level ("day", "week", "month", "year").
            key: The period start date string.

        Returns:
            A SummaryUnit instance, or None if not found.

        Raises:
            ProgressError: If the database query fails.
        """
        try:
            return get_summary(self._conn, unit, key)
        except DatabaseError as e:
            raise ProgressError(str(e)) from e

    def load_all_units(self, unit: str) -> list[SummaryUnit]:
        """Load all saved summaries for a given level, sorted by period_start.

        Args:
            unit: The summary level ("day", "week", "month", "year").

        Returns:
            A list of SummaryUnit instances, sorted by period_start.

        Raises:
            ProgressError: If the database query fails.
        """
        try:
            cursor = self._conn.execute(
                "SELECT data_json FROM summary WHERE level = ? ORDER BY period_start ASC",
                (unit,)
            )
            return [
                SummaryUnit.from_json(row[0])
                for row in cursor.fetchall()
            ]
        except (sqlite3.Error, DatabaseError) as e:
            raise ProgressError(str(e)) from e

    # ── Resume logic ───────────────────────────────────────────────────────

    def is_done(self, unit: str, key: str) -> bool:
        """Return True if this unit+key has already been completed.

        Args:
            unit: The summary level ("day", "week", "month", "year").
            key: The period start date string.

        Returns:
            True if a matching summary exists in the database.
        """
        try:
            cursor = self._conn.execute(
                "SELECT 1 FROM summary WHERE level = ? AND period_start = ?",
                (unit, key),
            )
            return cursor.fetchone() is not None
        except sqlite3.Error:
            return False

    def completed_keys(self, unit: str) -> set[str]:
        """Return the set of period_start keys already completed for a level.

        Args:
            unit: The summary level ("day", "week", "month", "year").

        Returns:
            A set of period start date strings.

        Raises:
            ProgressError: If the database query fails.
        """
        try:
            return set(list_summaries(self._conn, unit))
        except DatabaseError as e:
            raise ProgressError(str(e)) from e

    def reset(self, unit: str | None = None):
        """Delete all summary records for a given level, or all levels.

        Args:
            unit: The level to clear, or None to clear all summaries.

        Raises:
            ProgressError: If the database operation fails.
        """
        try:
            if unit is None:
                self._conn.execute("DELETE FROM summary")
            else:
                self._conn.execute(
                    "DELETE FROM summary WHERE level = ?", (unit,)
                )
            self._conn.commit()
        except sqlite3.Error as e:
            raise ProgressError(str(e)) from e

    def invalidate_unit(self, unit: str, key: str) -> bool:
        """Remove a summary from the database.

        Args:
            unit: The summary level ("day", "week", "month", "year").
            key: The period start date string.

        Returns:
            True if a matching summary was deleted.
        """
        try:
            cursor = self._conn.execute(
                "DELETE FROM summary WHERE level = ? AND period_start = ?",
                (unit, key),
            )
            self._conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error:
            return False

    # ── Status summary ─────────────────────────────────────────────────────

    def status_summary(self) -> dict:
        """Return a count of completed units by level.

        Returns:
            A dict mapping level names to counts, e.g. {"day": 100, "week": 15}.
        """
        try:
            cursor = self._conn.execute(
                "SELECT level, COUNT(*) FROM summary GROUP BY level"
            )
            return {row[0]: row[1] for row in cursor.fetchall()}
        except sqlite3.Error:
            return {}
