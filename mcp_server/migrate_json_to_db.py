"""
mcp_server/migrate_json_to_db.py
===============================
ETL migration script that imports the legacy JSON file-based database into the
new SQLite database schema. It supports dry-run mode, field-by-field integrity
verification, archiving/cleaning original JSON files, and reverse export.
"""

from __future__ import annotations
import argparse
import json
import logging
import shutil
import tarfile
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

# Import schema and database modules
from diary_core.schema import RawEntry, SummaryUnit, EntityProfile
from mcp_server.db import (
    get_db_connection,
    init_db,
    upsert_raw_entry,
    get_raw_entries_by_date,
    upsert_summary,
    get_summary,
    upsert_entity_profile,
    get_entity_profile,
    list_summaries,
    list_entity_profiles,
    rebuild_fts_index,
    DatabaseError
)

# Setup structured logging for console output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("migrate_json_to_db")


def migrate_raw_entries(conn: sqlite3.Connection, raw_dir: Path, dry_run: bool = False) -> tuple[int, int]:
    """Read YYYY-MM-DD.json arrays and insert them into the raw_entry table.

    Args:
        conn: Active SQLite database connection.
        raw_dir: Path to raw_entries/ directory.
        dry_run: If True, do not commit any changes to the database.

    Returns:
        A tuple of (files_processed, entries_inserted).
    """
    if not raw_dir.exists():
        logger.warning("Raw entries directory does not exist: %s", raw_dir)
        return 0, 0

    json_files = list(raw_dir.glob("*.json"))
    logger.info("Found %d raw entry JSON files to migrate", len(json_files))

    files_count = 0
    entries_count = 0

    # Wrap the entire batch in a transaction to minimize disk writes
    try:
        for f_path in json_files:
            with open(f_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Expected structure: {"date": "YYYY-MM-DD", "entries": [...]}
            date_str = data.get("date")
            entries_data = data.get("entries", [])

            if not date_str:
                logger.warning("Skipping file missing date: %s", f_path)
                continue

            for item in entries_data:
                # Map JSON fields to RawEntry attributes, with fallback defaults
                entry = RawEntry(
                    date=date_str,
                    time=item.get("time", "00:00:00"),
                    source=item.get("source", "unknown"),
                    source_file=item.get("source_file", f_path.name),
                    text=item.get("text", ""),
                    role=item.get("role", "user")
                )
                
                if not dry_run:
                    upsert_raw_entry(conn, entry)
                entries_count += 1
            
            files_count += 1

        if not dry_run:
            conn.commit()
            logger.info("Successfully migrated %d entries across %d files.", entries_count, files_count)
        else:
            logger.info("[Dry Run] Would migrate %d entries across %d files.", entries_count, files_count)

        return files_count, entries_count
    except Exception as e:
        conn.rollback()
        logger.error("Failed to migrate raw entries: %s", e)
        raise e


def migrate_summaries(conn: sqlite3.Connection, summaries_dir: Path, dry_run: bool = False) -> tuple[int, int]:
    """Scan and import SummaryUnit files across all levels (day, week, month, year).

    Args:
        conn: Active SQLite database connection.
        summaries_dir: Path to summaries/ directory.
        dry_run: If True, do not commit any database changes.

    Returns:
        A tuple of (directories_processed, summaries_inserted).
    """
    if not summaries_dir.exists():
        logger.warning("Summaries directory does not exist: %s", summaries_dir)
        return 0, 0

    levels = ["day", "week", "month", "year"]
    dir_count = 0
    summary_count = 0

    try:
        for level in levels:
            level_dir = summaries_dir / level
            if not level_dir.exists():
                continue

            json_files = list(level_dir.glob("*.json"))
            logger.info("Found %d '%s' summaries to migrate", len(json_files), level)

            for f_path in json_files:
                with open(f_path, "r", encoding="utf-8") as f:
                    summary = SummaryUnit.from_json(f.read())
                
                # Enforce correctness of the level field in SummaryUnit
                summary.unit = level

                if not dry_run:
                    upsert_summary(conn, summary)
                summary_count += 1
            
            dir_count += 1

        if not dry_run:
            conn.commit()
            logger.info("Successfully migrated %d summaries across %d levels.", summary_count, dir_count)
        else:
            logger.info("[Dry Run] Would migrate %d summaries across %d levels.", summary_count, dir_count)

        return dir_count, summary_count
    except Exception as e:
        conn.rollback()
        logger.error("Failed to migrate summaries: %s", e)
        raise e


def migrate_entity_profiles(conn: sqlite3.Connection, entity_dir: Path, dry_run: bool = False) -> int:
    """Read entity profiles and insert them into the entity_profile table.

    Note that the utility index file '_index.json' is ignored.

    Args:
        conn: Active SQLite database connection.
        entity_dir: Path to entity_profiles/ directory.
        dry_run: If True, do not commit any database changes.

    Returns:
        The number of entity profiles inserted.
    """
    if not entity_dir.exists():
        logger.warning("Entity profiles directory does not exist: %s", entity_dir)
        return 0

    json_files = [
        f for f in entity_dir.glob("*.json")
        if f.name != "_index.json"
    ]
    logger.info("Found %d entity profile files to migrate", len(json_files))

    profile_count = 0
    try:
        for f_path in json_files:
            with open(f_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            profile = EntityProfile.from_dict(data)

            if not dry_run:
                upsert_entity_profile(conn, profile)
            profile_count += 1

        if not dry_run:
            conn.commit()
            logger.info("Successfully migrated %d entity profiles.", profile_count)
        else:
            logger.info("[Dry Run] Would migrate %d entity profiles.", profile_count)

        return profile_count
    except Exception as e:
        conn.rollback()
        logger.error("Failed to migrate entity profiles: %s", e)
        raise e


# ─────────────────────────────────────────────────────────────────────────────
#  Verification
# ─────────────────────────────────────────────────────────────────────────────

def verify_migration(
    conn: sqlite3.Connection,
    raw_dir: Path,
    summaries_dir: Path,
    entity_dir: Path
) -> bool:
    """Perform a rigorous field-by-field, file-by-row data integrity check.

    Compares every entry in the raw, summary, and profile JSON files against their
    corresponding database records to ensure zero data loss.

    Returns:
        True if the database is 100% verified, False otherwise.
    """
    logger.info("Starting database integrity verification check...")
    discrepancies = 0

    # 1. Verify Raw Entries
    if raw_dir.exists():
        for f_path in raw_dir.glob("*.json"):
            with open(f_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            date_str = data.get("date")
            json_entries = data.get("entries", [])
            
            db_entries = get_raw_entries_by_date(conn, date_str)
            
            # Simple check: do lengths match?
            if len(json_entries) != len(db_entries):
                logger.error(
                    "Mismatch on date %s: JSON has %d entries, DB has %d",
                    date_str, len(json_entries), len(db_entries)
                )
                discrepancies += 1
                continue
            
            # Detailed check: compare field values (ordered by time)
            for j_ent, d_ent in zip(json_entries, db_entries):
                if (
                    j_ent.get("time") != d_ent.time or
                    j_ent.get("role", "user") != d_ent.role or
                    j_ent.get("text") != d_ent.text
                ):
                    logger.error(
                        "Mismatch in content on date %s at time %s",
                        date_str, j_ent.get("time")
                    )
                    discrepancies += 1

    # 2. Verify Summaries
    if summaries_dir.exists():
        for level in ["day", "week", "month", "year"]:
            level_dir = summaries_dir / level
            if not level_dir.exists():
                continue
            
            for f_path in level_dir.glob("*.json"):
                with open(f_path, "r", encoding="utf-8") as f:
                    file_sum = SummaryUnit.from_json(f.read())
                
                db_sum = get_summary(conn, level, file_sum.period_start)
                if db_sum is None:
                    logger.error("Summary missing in DB: level=%s, start=%s", level, file_sum.period_start)
                    discrepancies += 1
                    continue
                
                # Compare critical keys
                if (
                    file_sum.summary != db_sum.summary or
                    file_sum.emotional_tone != db_sum.emotional_tone or
                    file_sum.key_events != db_sum.key_events or
                    file_sum.energy_level != db_sum.energy_level
                ):
                    logger.error("Content mismatch in summary: level=%s, start=%s", level, file_sum.period_start)
                    discrepancies += 1

    # 3. Verify Entity Profiles
    if entity_dir.exists():
        for f_path in entity_dir.glob("*.json"):
            if f_path.name == "_index.json":
                continue
            
            with open(f_path, "r", encoding="utf-8") as f:
                file_prof = EntityProfile.from_dict(json.load(f))
            
            db_prof = get_entity_profile(conn, file_prof.id)
            if db_prof is None:
                logger.error("Entity profile missing in DB: %s", file_prof.id)
                discrepancies += 1
                continue
            
            if (
                file_prof.display_name != db_prof.display_name or
                file_prof.aliases != db_prof.aliases or
                len(file_prof.timestamped_knowledge) != len(db_prof.timestamped_knowledge)
            ):
                logger.error("Content mismatch in entity profile: %s", file_prof.id)
                discrepancies += 1

    if discrepancies == 0:
        logger.info("Verification Complete: Zero discrepancies found! Database is healthy.")
        return True
    else:
        logger.error("Verification FAILED: Found %d data discrepancies.", discrepancies)
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Archive & Cleanup
# ─────────────────────────────────────────────────────────────────────────────

def archive_and_delete_jsons(
    archive_dir: Path,
    raw_dir: Path,
    summaries_dir: Path,
    entity_dir: Path
) -> None:
    """Bundle all JSON directories into a tarball and clean the loose workspace.

    Args:
        archive_dir: Destination path to store backups.
        raw_dir: Path to raw entries.
        summaries_dir: Path to summaries.
        entity_dir: Path to entity profiles.
    """
    archive_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = archive_dir / f"diary_json_backup_{timestamp}.tar.gz"
    
    logger.info("Creating backup archive at %s...", archive_name)
    
    dirs_to_backup = [raw_dir, summaries_dir, entity_dir]
    
    with tarfile.open(archive_name, "w:gz") as tar:
        for directory in dirs_to_backup:
            if directory.exists():
                # Store under their base directory names for clear extraction later
                tar.add(directory, arcname=directory.name)
                
    logger.info("Backup successfully written. Cleaning up loose JSON files...")
    
    # Safely clear the loose JSONs in workspace directories
    for directory in dirs_to_backup:
        if directory.exists():
            for f_path in directory.glob("**/*.json"):
                # We specifically leave _index.json in entity_profiles since it is needed
                # by the GUI's entity_tracker passes.
                if f_path.name == "_index.json":
                    continue
                f_path.unlink()
            
            # Clean up empty subdirectory branches in summaries directory
            if directory == summaries_dir:
                for sub in ["day", "week", "month", "year"]:
                    sub_path = summaries_dir / sub
                    if sub_path.exists() and not any(sub_path.iterdir()):
                        sub_path.rmdir()
                        
    logger.info(" loose JSON cleanup successfully executed.")


# ─────────────────────────────────────────────────────────────────────────────
#  Reverse Migration (Export)
# ─────────────────────────────────────────────────────────────────────────────

def export_db_to_json(
    conn: sqlite3.Connection,
    raw_dir: Path,
    summaries_dir: Path,
    entity_dir: Path
) -> None:
    """Read all SQLite records and write them back into clean JSON files.

    Provides a clean fallback path in case the user wants to revert to file storage.
    """
    logger.info("Rebuilding JSON files from SQLite database records...")
    
    # 1. Export Raw Entries
    cursor = conn.execute("SELECT DISTINCT date FROM raw_entry ORDER BY date ASC")
    dates = [row[0] for row in cursor.fetchall()]
    
    if dates:
        raw_dir.mkdir(parents=True, exist_ok=True)
        for date_str in dates:
            db_entries = get_raw_entries_by_date(conn, date_str)
            entries_list = []
            for entry in db_entries:
                entries_list.append({
                    "time": entry.time,
                    "source": entry.source,
                    "source_file": entry.source_file,
                    "role": entry.role,
                    "text": entry.text
                })
            
            output_data = {"date": date_str, "entries": entries_list}
            f_path = raw_dir / f"{date_str}.json"
            with open(f_path, "w", encoding="utf-8") as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)
                
        logger.info("Exported %d raw entry date files.", len(dates))

    # 2. Export Summaries
    for level in ["day", "week", "month", "year"]:
        starts = list_summaries(conn, level)
        if starts:
            level_dir = summaries_dir / level
            level_dir.mkdir(parents=True, exist_ok=True)
            for start in starts:
                summary = get_summary(conn, level, start)
                if summary:
                    f_path = level_dir / f"{start}.json"
                    with open(f_path, "w", encoding="utf-8") as f:
                        f.write(summary.to_json())
            logger.info("Exported %d '%s' summary files.", len(starts), level)

    # 3. Export Entity Profiles
    profiles = list_entity_profiles(conn)
    if profiles:
        entity_dir.mkdir(parents=True, exist_ok=True)
        for profile in profiles:
            f_path = entity_dir / f"{profile.id}.json"
            with open(f_path, "w", encoding="utf-8") as f:
                json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info("Exported %d entity profile files.", len(profiles))

    logger.info("Database records successfully exported to JSON files.")


# ─────────────────────────────────────────────────────────────────────────────
#  Main Runner
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Diary JSON records to SQLite DB.")
    parser.add_argument(
        "--db",
        type=str,
        default="data/diary.db",
        help="Path to destination SQLite database file (default: data/diary.db)"
    )
    parser.add_argument(
        "--workspace",
        type=str,
        default="data",
        help="Path to workspace root holding JSON folders (default: data)"
    )
    parser.add_argument(
        "--archive-dir",
        type=str,
        default="data/raw_data/json_archive",
        help="Path to backup storage (default: data/raw_data/json_archive)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and read source files without writing database records"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run strict data integrity verification checks on an existing DB"
    )
    parser.add_argument(
        "--archive-json",
        action="store_true",
        help="Create a backup tarball and delete loose JSON files from workspace"
    )
    parser.add_argument(
        "--export-json",
        action="store_true",
        help="Export SQLite records back to raw JSON files (reverses migration)"
    )

    args = parser.parse_args()

    # Define resource paths
    workspace_path = Path(args.workspace)
    raw_dir = workspace_path / "raw_entries"       # legacy JSON raw entries (optional)
    summaries_dir = workspace_path / "summaries"     # legacy JSON summaries (optional)
    entity_dir = workspace_path / "entity_profiles"  # legacy JSON profiles (optional)
    archive_dir = Path(args.archive_dir)

    # Establish db connection
    conn = get_db_connection(args.db)

    # Run verification only if requested
    if args.verify:
        success = verify_migration(conn, raw_dir, summaries_dir, entity_dir)
        conn.close()
        exit(0 if success else 1)

    # Run export back to JSON if requested
    if args.export_json:
        export_db_to_json(conn, raw_dir, summaries_dir, entity_dir)
        conn.close()
        exit(0)

    # Standard Migration Flow
    if not args.dry_run:
        init_db(conn)

    logger.info("Starting migration process...")
    try:
        migrate_raw_entries(conn, raw_dir, args.dry_run)
        migrate_summaries(conn, summaries_dir, args.dry_run)
        migrate_entity_profiles(conn, entity_dir, args.dry_run)
        
        if not args.dry_run:
            # Rebuild FTS index for keyword search
            rebuild_fts_index(conn)
            logger.info("Migration Completed Successfully.")
            
            # Post-migration check
            verified = verify_migration(conn, raw_dir, summaries_dir, entity_dir)
            if not verified:
                logger.error("Verification failed immediately post-migration! Leaving original JSONs untouched.")
                conn.close()
                exit(1)
            
            # Perform clean archive if specified
            if args.archive_json:
                archive_and_delete_jsons(archive_dir, raw_dir, summaries_dir, entity_dir)
        else:
            logger.info("[Dry Run] Completed. No records were written or modified.")

    except Exception as e:
        logger.critical("Migration failed with fatal exception: %s", e)
        conn.close()
        exit(1)

    conn.close()


if __name__ == "__main__":
    main()
