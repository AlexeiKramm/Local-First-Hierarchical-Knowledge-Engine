"""
diary_ingestor/assembler.py
===========================
Merges staged entries from raw parsers into the SQLite database.
Deduplicates incoming entries against existing entries in the database,
ensuring idempotency and data integrity.
"""

from __future__ import annotations
import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

# Import canonical schemas and DB helpers
from diary_core.schema import RawEntry
from mcp_server.db import (
    get_db_connection,
    init_db,
    upsert_raw_entry,
    generate_entry_id,
    DatabaseError
)

logger = logging.getLogger(__name__)

LogCallback = Callable[[str], None]


class Assembler:
    """Orchestrates merging parsed/staged raw diary logs into the SQLite store."""

    def __init__(
        self,
        raw_entries_dir: str | Path,
        log: Optional[LogCallback] = None,
    ):
        """Initialize the Assembler, resolving directory paths to database files.

        Args:
            raw_entries_dir: Path to raw entries directory or the SQLite database.
                For backward compatibility, directory paths are resolved to
                'diary.db' in the parent directory.
            log: Optional callback function to receive status update messages.
        """
        raw_path = Path(raw_entries_dir)
        
        # Compatibility resolution: if a directory is passed (like raw_entries/),
        # resolve it to the database file in the parent folder.
        if raw_path.is_dir() or raw_path.name == "raw_entries":
            self.db_path = raw_path.parent / "diary.db"
        else:
            self.db_path = raw_path
            
        self.log = log or (lambda m: None)

    def assemble(self, staged_entries: list[dict]) -> dict[str, int]:
        """Merge staged entries into the raw_entry database table.

        Performs O(N) date-grouped query checks to deduplicate incoming entries.

        Args:
            staged_entries: List of dictionaries matching RawEntry properties
                (date, time, source, source_file, role, text).

        Returns:
            A status dictionary:
            {"new_dates": int, "appended": int, "skipped_duplicates": int}

        Raises:
            DatabaseError: If database insertion fails.
        """
        if not staged_entries:
            return {"new_dates": 0, "appended": 0, "skipped_duplicates": 0}

        # 1. Group staged entries by date
        by_date: dict[str, list[dict]] = defaultdict(list)
        for e in staged_entries:
            date = e.get("date")
            if not date:
                continue
            by_date[date].append(e)

        new_dates = 0
        appended = 0
        skipped = 0

        # Connect and initialize database if needed
        conn = get_db_connection(self.db_path)
        init_db(conn)

        try:
            # Wrap in a single transaction for maximum speed
            with conn:
                for date_str, new_items in sorted(by_date.items()):
                    # Query existing entry IDs for this date in one quick check
                    cursor = conn.execute("SELECT id FROM raw_entry WHERE date = ?", (date_str,))
                    existing_ids = {row[0] for row in cursor.fetchall()}
                    
                    if not existing_ids:
                        new_dates += 1

                    added_this_day = 0
                    for item in new_items:
                        # Construct temporary RawEntry to compute its deterministic ID
                        entry = RawEntry(
                            date=date_str,
                            time=item.get("time", "00:00:00"),
                            source=item.get("source", "unknown"),
                            source_file=item.get("source_file", ""),
                            text=item.get("text", ""),
                            role=item.get("role", "user")
                        )
                        entry_id = generate_entry_id(entry)

                        # Skip if this specific entry is already in the database
                        if entry_id in existing_ids:
                            skipped += 1
                            continue

                        # Insert into database
                        upsert_raw_entry(conn, entry)
                        existing_ids.add(entry_id)
                        added_this_day += 1
                        appended += 1

                    if added_this_day > 0:
                        status_label = "[NEW]" if new_dates and added_this_day == len(new_items) else "[UPD]"
                        self.log(f"  {status_label} {date_str} in DB — +{added_this_day} entries")

            logger.info("Assembly transaction complete: %d records added.", appended)
            return {
                "new_dates": new_dates,
                "appended": appended,
                "skipped_duplicates": skipped
            }
        except sqlite3.Error as e:
            logger.error("Failed to assemble staged entries to database: %s", e)
            raise DatabaseError(f"Assembly failed: {e}") from e
        finally:
            conn.close()

#TODO Will be deleted in the future, once the summarization part is made to work by reading data from the SQLite database, thus making the merged_diary.json unnecessary.
# First the database summariser should be ensured to function correctly.
    def regenerate_merged(self, output_path: str | Path) -> int:
        """Query raw_entry table and generate a monolithic merged_diary.json file.

        Ensures full backwards compatibility with legacy analysis scripts.

        Args:
            output_path: Destination path to save the merged JSON file.

        Returns:
            The total number of entries written.
        """
        output_path = Path(output_path)
        logger.info("Regenerating monolithic merged JSON at %s", output_path)

        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.execute(
                """
                SELECT date, time, source, source_file, role, text
                FROM raw_entry
                ORDER BY date ASC, time ASC
                """
            )
            rows = cursor.fetchall()
            
            by_date = defaultdict(list)
            for row in rows:
                date, time, source, source_file, role, text = row
                by_date[date].append({
                    "time": time,
                    "source": source,
                    "source_file": source_file or "",
                    "role": role,
                    "text": text
                })

            merged_entries = [
                {"date": d, "entries": entries}
                for d, entries in sorted(by_date.items())
            ]

            total = sum(len(d["entries"]) for d in merged_entries)
            dates = [d["date"] for d in merged_entries]
            date_range = {"start": dates[0], "end": dates[-1]} if dates else {}

            output = {
                "metadata": {
                    "created": datetime.now().isoformat(timespec="seconds"),
                    "total_days": len(merged_entries),
                    "total_entries": total,
                    "date_range": date_range,
                    "format_version": "2.0",
                },
                "entries": merged_entries,
            }

            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)

            self.log(f"  ✓ Regenerated merged JSON — {len(merged_entries)} days, {total} entries → {output_path.name}")
            return total
        except (sqlite3.Error, OSError) as e:
            logger.error("Failed to regenerate merged JSON: %s", e)
            raise DatabaseError(f"Regeneration of merged JSON failed: {e}") from e
        finally:
            conn.close()
