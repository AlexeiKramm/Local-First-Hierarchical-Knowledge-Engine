"""
mcp_server/db.py
===============
Database bridge layer for the Diary Analyzer. Handles SQLite connections,
schema initialization, migrations, and CRUD operations for raw entries,
summaries, and entity profiles, mapping them to and from canonical dataclasses.
"""

from __future__ import annotations
import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

# Import canonical schemas from the core diary_core package
from diary_core.schema import RawEntry, SummaryUnit, EntityProfile

# Setup structured logging
logger = logging.getLogger(__name__)

# Canonical schema version for migration tracking
SCHEMA_VERSION = 1


class DatabaseError(Exception):
    """Base exception for all database operations."""
    pass


class SchemaMigrationError(DatabaseError):
    """Raised when database schema initialization or migrations fail."""
    pass


def get_db_connection(db_path: Path | str) -> sqlite3.Connection:
    """Establish a connection to the SQLite database and apply pragmas.

    Does NOT attempt to change the journal mode. WAL mode is incompatible with
    NFS/SMB NAS mounts (broken shared-memory locking), so the journal mode must
    be managed separately and deliberately (use DELETE mode for network-hosted
    databases). This function leaves whatever journal mode is already set in the
    DB file intact.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        An active sqlite3.Connection object.

    Raises:
        DatabaseError: If the connection fails or settings cannot be applied.
    """
    db_path = Path(db_path)
    logger.info("Connecting to database at %s", db_path)

    try:
        # Create parent directories if they do not exist
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Apply safe pragmas that work on any filesystem.
        # We deliberately do NOT set journal_mode here: WAL mode breaks on NAS/NFS
        # mounts because those filesystems do not support the shared-memory locking
        # that WAL requires. Journal mode is a persistent DB-file setting; change
        # it once with the set_journal_mode() helper when creating or migrating the DB.
        for pragma, value in [("PRAGMA synchronous", "NORMAL"),
                              ("PRAGMA foreign_keys", "ON")]:
            try:
                conn.execute(f"{pragma} = {value}")
            except sqlite3.Error as e:
                logger.debug("Pragma skipped (%s = %s): %s", pragma, value, e)

        return conn
    except (sqlite3.Error, OSError) as e:
        logger.error("Failed to connect to database at %s: %s", db_path, e)
        raise DatabaseError(f"Database connection failed: {e}") from e


def set_journal_mode(db_path: Path | str, mode: str = "delete") -> str:
    """Permanently change the journal mode stored in the SQLite database file.

    This is a one-time administrative operation. The mode persists in the file
    header, so all future connections (from any machine) will use it.

    Use ``mode='delete'`` (the default) for databases stored on NAS/NFS mounts.
    Use ``mode='wal'`` only for databases on a local SSD with a single writer.

    Args:
        db_path: Path to the SQLite database file.
        mode: Target journal mode string, e.g. ``'delete'`` or ``'wal'``.

    Returns:
        The journal mode that SQLite actually applied (may differ from requested).

    Raises:
        DatabaseError: If the connection or PRAGMA execution fails.
    """
    db_path = Path(db_path)
    try:
        conn = sqlite3.connect(str(db_path))
        actual = conn.execute(f"PRAGMA journal_mode = {mode}").fetchone()[0]
        conn.close()
        if actual != mode:
            logger.warning(
                "Requested journal mode '%s' but got '%s' (filesystem may not support it).",
                mode, actual,
            )
        else:
            logger.info("Journal mode set to '%s' for %s", actual, db_path)
        return actual
    except (sqlite3.Error, OSError) as e:
        raise DatabaseError(f"Failed to set journal mode: {e}") from e


def init_db(conn: sqlite3.Connection) -> None:
    """Initialize database tables, indexes, and virtual tables if not present.

    Ensures tables exist for raw entries, summaries, entity profiles, FTS5 virtual index,
    and a metadata table tracking the schema version. If tables exist, verifies version.

    Args:
        conn: An active SQLite database connection.

    Raises:
        SchemaMigrationError: If table creation fails or version mismatches.
    """
    logger.info("Initializing database schema...")
    try:
        with conn:
            # 1. Metadata table for schema versioning
            conn.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            # Check existing version
            cursor = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'")
            row = cursor.fetchone()
            if row is not None:
                current_version = int(row[0])
                logger.info("Database schema version is %d", current_version)
                if current_version > SCHEMA_VERSION:
                    raise SchemaMigrationError(
                        f"Database schema version {current_version} is newer than "
                        f"supported version {SCHEMA_VERSION}. Please update your software."
                    )
                # Future migration logic would go here (e.g. if current_version < SCHEMA_VERSION)
            else:
                # Set initial version
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),)
                )

            # 2. Raw entry table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS raw_entry (
                    id           TEXT PRIMARY KEY,
                    date         TEXT NOT NULL,
                    time         TEXT NOT NULL,
                    source       TEXT NOT NULL,
                    source_file  TEXT,
                    role         TEXT NOT NULL DEFAULT 'user',
                    text         TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_entry_date ON raw_entry(date)")

            # 3. Summary table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS summary (
                    level        TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end   TEXT,
                    data_json    TEXT NOT NULL,
                    PRIMARY KEY (level, period_start)
                )
            """)

            # 4. Entity profile table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entity_profile (
                    id           TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    aliases_json TEXT DEFAULT '[]',
                    data_json    TEXT NOT NULL
                )
            """)

            # 5. FTS5 Virtual Table for full-text search
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(
                    source_type UNINDEXED,
                    source_id   UNINDEXED,
                    date        UNINDEXED,
                    content,
                    tokenize='unicode61 remove_diacritics 1'
                )
            """)

        logger.info("Database schema successfully initialized.")
    except sqlite3.Error as e:
        logger.error("Failed to initialize database schema: %s", e)
        raise SchemaMigrationError(f"Database schema initialization failed: {e}") from e


def generate_entry_id(entry: RawEntry) -> str:
    """Generate a stable, deterministic 16-character SHA-256 hash for deduplication.

    Args:
        entry: The RawEntry instance to hash.

    Returns:
        A 16-character hexadecimal hash string.
    """
    hash_input = f"{entry.date}|{entry.time}|{entry.source_file or ''}|{entry.role}|{entry.text}"
    return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
#  Raw Entry CRUD Helpers
# ─────────────────────────────────────────────────────────────────────────────

def upsert_raw_entry(conn: sqlite3.Connection, entry: RawEntry) -> str:
    """Insert or replace a RawEntry in the database.

    Uses a deterministic SHA-256 hash derived from the entry fields as its ID.

    Args:
        conn: An active SQLite database connection.
        entry: The RawEntry to insert or update.

    Returns:
        The generated 16-character entry ID.

    Raises:
        DatabaseError: If the insert operation fails.
    """
    entry_id = generate_entry_id(entry)
    try:
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO raw_entry (id, date, time, source, source_file, role, text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    entry.date,
                    entry.time,
                    entry.source,
                    entry.source_file,
                    entry.role,
                    entry.text
                )
            )
        return entry_id
    except sqlite3.Error as e:
        logger.error("Failed to upsert raw entry for date %s: %s", entry.date, e)
        raise DatabaseError(f"Failed to upsert raw entry: {e}") from e


def get_raw_entry_by_id(conn: sqlite3.Connection, entry_id: str) -> RawEntry | None:
    """Retrieve a RawEntry by its deterministic ID.

    Args:
        conn: An active SQLite database connection.
        entry_id: The 16-character entry ID.

    Returns:
        A RawEntry instance, or None if no match is found.

    Raises:
        DatabaseError: If the query fails.
    """
    try:
        cursor = conn.execute(
            """
            SELECT date, time, source, source_file, text, role
            FROM raw_entry WHERE id = ?
            """,
            (entry_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return RawEntry(
            date=row[0],
            time=row[1],
            source=row[2],
            source_file=row[3] or "",
            text=row[4],
            role=row[5]
        )
    except sqlite3.Error as e:
        logger.error("Failed to retrieve raw entry with ID %s: %s", entry_id, e)
        raise DatabaseError(f"Failed to retrieve raw entry by ID: {e}") from e


def get_raw_entries_by_date(conn: sqlite3.Connection, date: str) -> list[RawEntry]:
    """Retrieve all RawEntry items for a specific YYYY-MM-DD date.

    Args:
        conn: An active SQLite database connection.
        date: The date string (YYYY-MM-DD).

    Returns:
        A list of RawEntry instances, ordered by time.

    Raises:
        DatabaseError: If the query fails.
    """
    try:
        cursor = conn.execute(
            """
            SELECT date, time, source, source_file, text, role
            FROM raw_entry WHERE date = ? ORDER BY time ASC
            """,
            (date,)
        )
        return [
            RawEntry(
                date=row[0],
                time=row[1],
                source=row[2],
                source_file=row[3] or "",
                text=row[4],
                role=row[5]
            )
            for row in cursor.fetchall()
        ]
    except sqlite3.Error as e:
        logger.error("Failed to retrieve raw entries for date %s: %s", date, e)
        raise DatabaseError(f"Failed to retrieve raw entries by date: {e}") from e


# ─────────────────────────────────────────────────────────────────────────────
#  Summary CRUD Helpers
# ─────────────────────────────────────────────────────────────────────────────

def upsert_summary(conn: sqlite3.Connection, summary: SummaryUnit) -> None:
    """Insert or replace a SummaryUnit in the database.

    Args:
        conn: An active SQLite database connection.
        summary: The SummaryUnit to upsert.

    Raises:
        DatabaseError: If the upsert fails.
    """
    try:
        data_json = summary.to_json()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO summary (level, period_start, period_end, data_json)
                VALUES (?, ?, ?, ?)
                """,
                (summary.unit, summary.period_start, summary.period_end, data_json)
            )
    except sqlite3.Error as e:
        logger.error("Failed to upsert summary level=%s, start=%s: %s", summary.unit, summary.period_start, e)
        raise DatabaseError(f"Failed to upsert summary: {e}") from e


def get_summary(conn: sqlite3.Connection, level: str, period_start: str) -> SummaryUnit | None:
    """Retrieve a SummaryUnit by level and period start.

    Args:
        conn: An active SQLite database connection.
        level: The summary level ("day", "week", "month", "year").
        period_start: The period start date (YYYY-MM-DD or YYYY-MM or YYYY).

    Returns:
        A SummaryUnit instance, or None if not found.

    Raises:
        DatabaseError: If the query or JSON parsing fails.
    """
    try:
        cursor = conn.execute(
            "SELECT data_json FROM summary WHERE level = ? AND period_start = ?",
            (level, period_start)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return SummaryUnit.from_json(row[0])
    except (sqlite3.Error, json.JSONDecodeError, ValueError) as e:
        logger.error("Failed to retrieve summary level=%s, start=%s: %s", level, period_start, e)
        raise DatabaseError(f"Failed to retrieve summary: {e}") from e


def list_summaries(conn: sqlite3.Connection, level: str) -> list[str]:
    """Retrieve a sorted list of period_start dates for a given summary level.

    Args:
        conn: An active SQLite database connection.
        level: The summary level ("day", "week", "month", "year").

    Returns:
        A sorted list of period start date strings.

    Raises:
        DatabaseError: If the query fails.
    """
    try:
        cursor = conn.execute(
            "SELECT period_start FROM summary WHERE level = ? ORDER BY period_start ASC",
            (level,)
        )
        return [row[0] for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logger.error("Failed to list summaries for level %s: %s", level, e)
        raise DatabaseError(f"Failed to list summaries: {e}") from e


# ─────────────────────────────────────────────────────────────────────────────
#  Entity Profile CRUD Helpers
# ─────────────────────────────────────────────────────────────────────────────

def upsert_entity_profile(conn: sqlite3.Connection, profile: EntityProfile) -> None:
    """Insert or replace an EntityProfile in the database.

    Args:
        conn: An active SQLite database connection.
        profile: The EntityProfile to upsert.

    Raises:
        DatabaseError: If the upsert fails.
    """
    try:
        aliases_json = json.dumps(profile.aliases, ensure_ascii=False)
        data_json = json.dumps(profile.to_dict(), ensure_ascii=False)
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO entity_profile (id, display_name, aliases_json, data_json)
                VALUES (?, ?, ?, ?)
                """,
                (profile.id, profile.display_name, aliases_json, data_json)
            )
    except sqlite3.Error as e:
        logger.error("Failed to upsert entity profile %s: %s", profile.id, e)
        raise DatabaseError(f"Failed to upsert entity profile: {e}") from e


def get_entity_profile(conn: sqlite3.Connection, entity_id: str) -> EntityProfile | None:
    """Retrieve an EntityProfile by its snake_case ID.

    Args:
        conn: An active SQLite database connection.
        entity_id: The snake_case identifier (e.g. "anna_korhonen").

    Returns:
        An EntityProfile instance, or None if not found.

    Raises:
        DatabaseError: If the query or deserialization fails.
    """
    try:
        cursor = conn.execute(
            "SELECT data_json FROM entity_profile WHERE id = ?",
            (entity_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return EntityProfile.from_dict(json.loads(row[0]))
    except (sqlite3.Error, json.JSONDecodeError, TypeError, ValueError) as e:
        logger.error("Failed to retrieve entity profile %s: %s", entity_id, e)
        raise DatabaseError(f"Failed to retrieve entity profile: {e}") from e


def list_entity_profiles(conn: sqlite3.Connection) -> list[EntityProfile]:
    """Retrieve a list of all entity profiles in the database.

    Returns:
        A list of EntityProfile instances.

    Raises:
        DatabaseError: If the query or deserialization fails.
    """
    try:
        cursor = conn.execute("SELECT data_json FROM entity_profile ORDER BY id ASC")
        return [
            EntityProfile.from_dict(json.loads(row[0]))
            for row in cursor.fetchall()
        ]
    except (sqlite3.Error, json.JSONDecodeError, TypeError, ValueError) as e:
        logger.error("Failed to list entity profiles: %s", e)
        raise DatabaseError(f"Failed to list entity profiles: {e}") from e


# ─────────────────────────────────────────────────────────────────────────────
#  Full Text Search (FTS5) Population
# ─────────────────────────────────────────────────────────────────────────────

def rebuild_fts_index(conn: sqlite3.Connection) -> None:
    """Wipe and completely rebuild the Full-Text Search index from source tables.

    This compiles and indexes all content strings from `raw_entry` and `summary`.

    Args:
        conn: An active SQLite database connection.

    Raises:
        DatabaseError: If the index rebuild fails.
    """
    logger.info("Rebuilding Full-Text Search (FTS5) index...")
    try:
        with conn:
            # Wipe existing index content
            conn.execute("DELETE FROM content_fts")

            # 1. Index raw entries
            cursor_raw = conn.execute("SELECT id, date, text FROM raw_entry")
            raw_data = [
                ("raw", row[0], row[1], row[2])
                for row in cursor_raw.fetchall()
            ]
            if raw_data:
                conn.executemany(
                    """
                    INSERT INTO content_fts (source_type, source_id, date, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    raw_data
                )

            # 2. Index summaries
            cursor_summary = conn.execute("SELECT level, period_start, data_json FROM summary")
            summary_data = []
            for row in cursor_summary.fetchall():
                level, period_start, data_json = row
                try:
                    summary_obj = json.loads(data_json)
                    content = summary_obj.get("summary") or ""
                    if content:
                        summary_data.append((f"summary_{level}", f"{level}:{period_start}", period_start, content))
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Skipping index insertion of malformed summary JSON for level=%s, start=%s", level, period_start)
            
            if summary_data:
                conn.executemany(
                    """
                    INSERT INTO content_fts (source_type, source_id, date, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    summary_data
                )

        logger.info("FTS5 index successfully rebuilt.")
    except sqlite3.Error as e:
        logger.error("Failed to rebuild FTS5 index: %s", e)
        raise DatabaseError(f"Failed to rebuild FTS5 index: {e}") from e
