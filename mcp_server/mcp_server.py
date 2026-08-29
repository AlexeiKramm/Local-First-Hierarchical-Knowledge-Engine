import json
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional

# Ensure the project root is on the path when run directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# We will use the FastMCP wrapper from the official Anthropic SDK
# Install via: pip install mcp
from mcp.server.fastmcp import FastMCP

from mcp_server.db import (
    DatabaseError,
    get_db_connection,
    get_entity_profile as db_get_entity_profile,
    get_raw_entries_by_date,
    get_summary as db_get_summary,
    list_entity_profiles,
    list_summaries as db_list_summaries,
)

# Define the server
mcp = FastMCP("Personal Historian Diary MCP", dependencies=["mcp"])

# ─── Path Configuration ───────────────────────────────────────────────────────
# Data directory at the project root
BASE_DIR = Path(__file__).resolve().parent.parent / "data"
# ─── Database Connection ──────────────────────────────────────────────────────
_DB_PATH = str(BASE_DIR / "diary.db")
_db_conn: sqlite3.Connection | None = None

def _get_db() -> sqlite3.Connection:
    """Get or lazily initialize the database connection.

    Returns the existing connection if still open, or attempts to reconnect.
    Prints errors to stderr so MCP client logs capture them.

    Returns:
        An active sqlite3.Connection.

    Raises:
        RuntimeError: If the connection cannot be established.
    """
    global _db_conn
    if _db_conn is not None:
        try:
            _db_conn.execute("SELECT 1")
            return _db_conn
        except sqlite3.Error:
            _db_conn.close()
            _db_conn = None

    try:
        _db_conn = get_db_connection(_DB_PATH)
        _db_conn.execute("SELECT 1")
        return _db_conn
    except (DatabaseError, sqlite3.Error) as e:
        db_path_str = str(_DB_PATH)
        print(f"WARN: Read-write connection failed ({e}), trying read-only...", file=sys.stderr)
        try:
            uri_path = db_path_str.replace("\\", "/")
            if ":" in uri_path and not uri_path.startswith("/"):
                uri_path = "/" + uri_path
            _db_conn = sqlite3.connect(f"file://{uri_path}?mode=ro", uri=True)
            _db_conn.row_factory = sqlite3.Row
            _db_conn.execute("SELECT 1")
            return _db_conn
        except sqlite3.Error as e2:
            print(f"ERROR: Failed to connect to database at {_DB_PATH}: {e2}", file=sys.stderr)
            raise RuntimeError(f"Database connection unavailable: {e2}") from e2



# ─── Helper Functions ─────────────────────────────────────────────────────────

def get_closest_files(target_file: str, available_files: List[str]) -> tuple[Optional[str], Optional[str]]:
    """
    Given a target (e.g., '2024-11-08') and a sorted list of available period starts,
    returns a tuple of (closest_previous_file, closest_next_file).
    """
    if not available_files:
        return None, None
    
    prev_file = None
    next_file = None
    
    for f in available_files:
        if f < target_file:
            prev_file = f
        elif f > target_file and next_file is None:
            next_file = f
            break  # Stop as soon as we find the first file greater than the target
            
    return prev_file, next_file

# ─── MCP Tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def get_diary_architecture_help() -> str:
    """
    CALL THIS FIRST — returns the full system manual for the Diary Database Analyzer.

    WHEN TO USE:
      - At the very start of any diary-related request, before calling any other tool.
      - When you are unsure which tool to call or how the data is structured.
      - When the user references past feelings, historical periods, or old behaviour
        patterns and you need orientation before diving in.

    RETURNS:
      A plain-text instruction manual covering: the data hierarchy (raw → day → week
      → month → year), the query-scope traversal rules, and anti-hallucination rules.
      Read it fully before proceeding.

    IMPORTANT: Do NOT skip this call. Without it you are likely to query wrong levels,
    guess dates, or miss the correct traversal depth.
    """
    return """
=== DIARY DATABASE — SYSTEM INSTRUCTIONS ===

Your role: Personal Historian. Explore the user's past to answer their questions
with appropriate depth — neither too shallow nor unnecessarily broad.

SUMMARIES ARE YOUR PRIMARY SOURCE.
For any query spanning more than a few days, summaries cover ~80% of what you need.
They are not shallow placeholders — they are structured, rich digests. Drill deeper
only when a concrete signal tells you to (see Section 2). Do not drill to raw entries
or all day summaries by default; doing so wastes context and can cause failures.


--- QUICK TOOL ROUTING ---

IF the user asks about a person / relationship / "how did things go with X"
  → list_entities() then get_entity_profile(entity_id)

IF the user asks about a time period ("how was my 2023?", "what happened in March?")
  → Determine the query scope (year / month / week / day) and follow Section 2.

IF the user asks whether they ever mentioned a topic, place, or keyword
  → search_full_text(query, scope="user")

IF the user wants exact words from a specific day
  → list_raw_files() then get_raw_entry([date])

IF you are unsure which dates exist or how the data is structured
  → list_summary_files("year") to get your bearings first


--- 1. DATA HIERARCHY ---

The diary is a cascading hierarchical database:

  year → month → week → day → raw (verbatim chat logs)

Each level summarises the level below it. You navigate top-down, starting one
level above the scope of the query. You stop when you have sufficient context —
not when you have read everything.


--- 2. QUERY-SCOPE TRAVERSAL RULES ---

Determine the temporal scope of the user's query (year / month / week / day)
and follow the matching rule. Always anchor one level above the query scope.

  YEAR-SCOPE query ("How was my 2024?" / "What changed in the past year?")
    ✓ Read the year summary
    ✓ Read ALL month summaries for that year
    ✓ Read week summaries for the 2–3 most significant months
      (flagged in month summaries by key_events or extreme scalar_metrics)
    ✗ Do NOT read day summaries or raw entries unless the user asks

  MONTH-SCOPE query ("How was my February?" / "Summarize the last 4 weeks")
    ✓ Read the year summary (context anchor)
    ✓ Read the month summary
    ✓ Read ALL week summaries in that month/range
    ✓ For each week, read day summaries for the 1–2 most notable days
      (select by: key_events matching the query topic, OR scalar_metrics ≤ 3 or ≥ 8)
    ✗ Do NOT read all day summaries; do NOT read raw entries unless the user asks

  WEEK-SCOPE query ("How was my week of June 2nd?")
    ✓ Read the month summary (context anchor)
    ✓ Read the week summary
    ✓ Read ALL day summaries for that week
    ✓ Read raw entries for 1–2 days that stand out by key_events or query relevance
    ✗ Do NOT read raw entries for every day

  DAY-SCOPE query ("How was my January 15th?" / "What happened on [date]?")
    ✓ Read the month summary (context anchor)
    ✓ Read the week summary containing that day
    ✓ Read the day summary
    ✓ Read the raw entry for that day — ALWAYS (it is the point of a day query)
    ✗ Do NOT read raw entries for adjacent days unless the user asks

  PERSON query ("How did things go with X?" / "Tell me about my relationship with Y")
    ✓ Call list_entities() → get_entity_profile(entity_id)
    ✓ Use search_full_text(name, scope="user") to find relevant dates
    ✓ Read day summaries for the most relevant dates from the evidence log
    ✗ Do NOT read entity arc alone — check the evidence log for specific events


--- 3. SIGNALS THAT JUSTIFY GOING ONE LEVEL DEEPER ---

Only drill beyond the scope-appropriate depth if you observe:
  • key_events in a summary directly match the user's query topic
  • scalar_metrics Energy / Social / Momentum ≤ 3 or ≥ 8 on a specific day
  • The user explicitly asks for "exact words", "what did I write", "show me"
  • A summary narrative mentions a pivotal event you cannot describe from the summary


--- 4. WORKED EXAMPLES ---

EXAMPLE A — "How was my February 2026?" (month-scope)
  1. list_summary_files("year") → get_summary("year", ["2026"]) — context anchor
  2. list_summary_files("month") → get_summary("month", ["2026-02"]) — month overview
  3. list_summary_files("week") → get_summary("week", [all weeks in Feb 2026]) — all weeks
  4. For each week, check key_events and scalar_metrics → pick 1-2 notable days per week
  5. get_summary("day", [those 4-8 notable day dates]) — targeted day summaries
  6. Synthesize. Do NOT fetch all ~28 day summaries or any raw entries.

EXAMPLE B — "How was my week of June 2nd?" (week-scope)
  1. list_summary_files("month") → get_summary("month", ["2025-06"]) — context anchor
  2. list_summary_files("week") → get_summary("week", ["2025-06-02"]) — week summary
  3. list_summary_files("day") → get_summary("day", [all days in that week]) — all ~7 day summaries
  4. From day summaries, identify 1-2 stand-out days → get_raw_entry([those dates])
  5. Synthesize.

EXAMPLE C — "How was January 15th?" (day-scope)
  1. list_summary_files("month") → get_summary("month", ["2025-01"]) — context anchor
  2. list_summary_files("week") → get_summary("week", [week containing Jan 15]) — week context
  3. list_summary_files("day") → get_summary("day", ["2025-01-15"]) — day summary
  4. list_raw_files() → get_raw_entry(["2025-01-15"]) — always read raw for day queries
  5. Synthesize.

EXAMPLE D — "How was my 2024?" (year-scope)
  1. list_summary_files("year") → get_summary("year", ["2024"]) — year overview
  2. list_summary_files("month") → get_summary("month", [all 2024 months]) — all months
  3. From month summaries, identify 2-3 most significant months
  4. list_summary_files("week") → get_summary("week", [weeks in those months]) — targeted weeks
  5. Synthesize. Do NOT read day summaries or raw entries.


--- 5. WHAT IS INSIDE A SUMMARY FILE ---

Every day/week/month/year summary contains structured metadata you can act on:

  summary             — narrative overview of the period
  emotional_tone      — dominant mood of the period
  key_events          — list of significant happenings; primary filter for drilling deeper
  scalar_metrics      — Energy Level, Social Connectedness, Forward Momentum
                         (0-10 scores). Scores ≤ 3 or ≥ 8 are notable signals
  narrative_threads   — ongoing life arcs (open/closed); great for context
  growth_markers      — positive developments the user may not have noticed
  coping_mechanisms   — how the user dealt with stress in this period
  avoidance_signals   — things the user was steering away from
  questions_raised    — unresolved tensions the user was sitting with


--- 6. TRAVERSAL PIPELINE (follow this order) ---

STEP A — ORIENT YOURSELF
  - Determine the query scope (year / month / week / day).
  - Never guess filenames. Call list_summary_files(level) to see what exists.
  - For a person query, call list_entities() first to get the canonical ID.

STEP B — ANCHOR AND DRILL (scope-appropriate depth)
  - Start one level above the query scope (see Section 2).
  - Read the required levels for your scope. Batch multiple dates in one call.
  - After each level, check key_events and scalar_metrics to decide if drilling
    deeper is justified by a concrete signal (see Section 3).

STEP C — KEYWORD / TOPIC SEARCH (when needed)
  - If the user asks "did I ever mention X?", run search_full_text(query).
  - Use scope="user" to search only the user's own words (filters AI replies).
  - Use date_from / date_to to narrow results if the hit count is large.

STEP D — HIGH-DEFINITION EXTRACTION (targeted, not exhaustive)
  - Read raw entries ONLY for: day-scope queries (always), or when key_events
    or scalar_metrics justify it, or when the user explicitly asks.
  - Raw entries are large — only fetch when you have a precise date target.
  - Batch multiple dates in one call.


--- 7. WHEN TO STOP ---

Stop reading when you have covered the levels required by your query scope.
Do NOT continue drilling to be thorough — unnecessary depth consumes context
that cannot be recovered and can cause request failures.

You have enough context when:
  • You have read all required levels for the query scope (Section 2).
  • You can cite specific dates, events, and emotions relevant to the query.
  • No concrete signal from Section 3 is calling you deeper.


--- 8. ANTI-HALLUCINATION RULES ---

  - NEVER invent or guess a filename. Always verify with list_summary_files first.
  - If a tool returns status "not_found", READ the smart_fallback_hint field.
    It tells you the nearest available previous and next dates. Use those.
  - Batch your requests. Pass a list of dates to get_summary or get_raw_entry
    rather than making one call per date — saves round-trips and context.
"""

@mcp.tool()
def list_entities() -> str:
    """
    Returns a directory of every tracked person in the user's life (IDs, roles, mention counts).

    WHEN TO USE:
      - Whenever the user mentions any person by name, nickname, or role
        (e.g. "my friend Alex", "my boss", "that girl I dated").
      - Always call this BEFORE calling get_entity_profile — you need the
        canonical ID first.
      - When the user asks "who have I written about most?" or "who are
        the important people in my life?"

    DO NOT USE when you already have the canonical entity ID from a previous
    call in the same session — skip straight to get_entity_profile.

    RETURNS:
      JSON array sorted by mention count (most-mentioned first). Each item has:
      `id` (use this in get_entity_profile), `role`, `mentions`.
    """
    try:
        conn = _get_db()
    except RuntimeError as e:
        return f"ERROR: {e}"

    try:
        profiles = list_entity_profiles(conn)
    except DatabaseError:
        return "ERROR: Could not read entity profiles from database."

    if not profiles:
        return "No entity profiles found."

    entities = []
    for p in profiles:
        mention_count = p.stable_facts.get("mention_count", len(p.timestamped_knowledge))
        entities.append({
            "id": p.id,
            "role": p.role_in_authors_life,
            "mentions": mention_count,
        })

    entities.sort(key=lambda x: -x["mentions"])
    return json.dumps(entities, indent=2)

@mcp.tool()
def get_entity_profile(entity_id: str, as_of_date: str = None, mode: str = "full") -> str:
    """
    Fetches the full relationship arc and chronological evidence log for a specific person.

    WHEN TO USE:
      - Any time the user asks about a specific person, their relationship with
        someone, or how someone has changed over time.
      - When the user asks "how did things go with X?", "what do I know about Y?",
        "when did Z and I stop talking?"
      - After calling list_entities() to obtain the canonical entity_id.
      - Aliases are resolved automatically — pass any known alias.

    DO NOT USE without first calling list_entities() to get the correct canonical ID.

    ARGS:
      entity_id:   Canonical ID or alias from list_entities(). Required.
      as_of_date:  ISO date "YYYY-MM-DD". If set, hides evidence after this date
                   (use when answering questions framed in the past, e.g. "as of
                   last March, what did I think of X?").
      mode:        Controls how much data is returned:
                     "full"     — arc summary + full evidence log (default, use normally).
                     "arc"      — relationship arc narrative only; use when the user
                                  wants an overview or history of the relationship.
                     "mentions" — chronological evidence quotes only; use when the
                                  user wants specific moments or proof of something.

    RETURNS:
      JSON with `arc_summary` (narrative text) and/or `evidence_log` (list of
      timestamped entries). Use arc_summary to answer broad questions; drill into
      evidence_log for specific events.
    """
    if mode not in ("full", "arc", "mentions"):
        return f"ERROR: Unknown mode '{mode}'. Valid: 'full', 'arc', 'mentions'."

    try:
        conn = _get_db()
    except RuntimeError as e:
        return f"ERROR: {e}"

    # Step 1: exact ID match
    profile = None
    try:
        profile = db_get_entity_profile(conn, entity_id)
    except DatabaseError:
        return f"ERROR: Database error while retrieving entity profile for '{entity_id}'."

    # Step 2: alias fallback (silent — no extra tool call needed)
    if profile is None:
        try:
            all_profiles = list_entity_profiles(conn)
        except DatabaseError:
            return "ERROR: Database error while searching aliases."

        entity_lower = entity_id.lower()
        for p in all_profiles:
            if entity_lower in [a.lower() for a in p.aliases]:
                profile = p
                break

    # Step 3: not found
    if profile is None:
        return (
            f"ERROR: Entity ID or alias '{entity_id}' not found. "
            f"Call list_entities() to see available IDs."
        )

    # Build response from the resolved profile
    try:
        profile_dict = profile.to_dict()
        all_entries = profile_dict.get("timestamped_knowledge", [])

        if as_of_date:
            entries_up_to = [e for e in all_entries if e.get("valid_from", "") <= as_of_date]
        else:
            entries_up_to = all_entries

        result = {}
        if mode in ("full", "arc"):
            result["arc_summary"] = profile_dict.get("relationship_arc_summary", "(no arc generated)")
        if mode in ("full", "mentions"):
            result["evidence_log"] = entries_up_to

        return json.dumps(result, indent=2)

    except Exception as e:
        return f"ERROR: Could not read profile. Details: {str(e)}"

@mcp.tool()
def get_summary(level: str, target_dates: list[str]) -> str:
    """
    Retrieves one or more AI-generated diary summaries at a chosen time level.

    Summaries are your primary source of information — they are structured, rich
    digests that cover the vast majority of what you need for most queries.
    Use get_diary_architecture_help() to determine the correct depth for your
    query scope before deciding what to fetch next.

    WHEN TO USE:
      - When the user asks about a specific past period: "How was my January?",
        "What happened in 2023?", "How was that week I was sick?"
      - When the user asks "what was I like back then?", "how have I changed
        since [period]?", or "what was going on in my life during [timeframe]?"
      - ALWAYS batch multiple dates into a single call (pass a list) rather than
        making repeated single-date calls.

    DEPTH GUIDE — scope-appropriate next steps after this call:
      After reading a YEAR summary   → read all MONTH summaries for that year;
                                       then week summaries for the 2–3 most
                                       significant months only.
      After reading a MONTH summary  → read all WEEK summaries in that month;
                                       then day summaries for 1–2 notable days
                                       per week (flagged by key_events or
                                       scalar_metrics ≤ 3 or ≥ 8).
      After reading a WEEK summary   → read ALL DAY summaries for that week;
                                       then raw entries for 1–2 stand-out days.
      After reading a DAY summary    → read the raw entry for that day
                                       (always, for day-scope queries).

    DO NOT USE without first calling list_summary_files(level) to verify which
    filenames actually exist. Do NOT guess or invent dates.
    DO NOT use for keyword or topic searches — use search_full_text instead.
    DO NOT use to look up a person — use get_entity_profile instead.

    ARGS:
      level:        Granularity of summary to fetch. One of:
                      "day"   — a single calendar day.
                      "week"  — a week period.
                      "month" — a full calendar month.
                      "year"  — a full calendar year.
      target_dates: List of period start strings as returned by list_summary_files.
                    Examples:
                      Months : ["2021-03", "2021-05", "2021-06"]
                      Years  : ["2021", "2022"]
                      Days   : ["2024-11-08"]

    RETURNS:
      JSON array — one entry per requested date. Each entry has `status` ("success"
      or "not_found") and either `data` (the full summary object) or
      `smart_fallback_hint` (the nearest available dates to retry with).
      If status is "not_found", READ the smart_fallback_hint and retry with the
      suggested date instead of giving up.
    """
    if level not in ("day", "week", "month", "year"):
        return f"ERROR: Invalid level '{level}'. Choose 'day', 'week', 'month', 'year'."

    try:
        conn = _get_db()
    except RuntimeError as e:
        return f"ERROR: {e}"

    try:
        available_periods = db_list_summaries(conn, level)
    except DatabaseError as e:
        return f"ERROR: Could not list summaries: {e}"
    
    responses = []
    
    for period_start in target_dates:
        summary = db_get_summary(conn, level, period_start)
        if summary is not None:
            responses.append({
                "request": period_start,
                "status": "success",
                "data": summary.to_dict()
            })
        else:
            prev_f, next_f = get_closest_files(period_start, available_periods)
            msg = f"No summary found at {level} level for '{period_start}'."
            if prev_f or next_f:
                msg += f" Closest available periods are: PREVIOUS=({prev_f or 'None'}), NEXT=({next_f or 'None'})."
            else:
                msg += " No summaries exist at this level."
            
            responses.append({
                "request": period_start,
                "status": "not_found",
                "smart_fallback_hint": msg
            })
            
    return json.dumps(responses, indent=2)

@mcp.tool()
def list_summary_files(level: str) -> str:
    """
    Lists all available summary period starts for a given time level — your navigation compass.

    WHEN TO USE:
      - ALWAYS call this before calling get_summary to discover which periods exist.
      - When the user asks "what years do you have data for?", "which months are
        available?", or "how far back does my diary go?"
      - When you need to enumerate a range of periods to batch-fetch.

    DO NOT skip this step and guess periods — missing entries return "not_found"
    and waste a round-trip. Call this first, always.

    CHOOSING THE RIGHT LEVEL TO LIST:
      - For year- or month-scope queries, list at "month" or "week" level — you
        rarely need to enumerate every individual day for a broad query.
      - Only list at "day" level when you have already narrowed to a specific week
        or when the query is day-scope. Listing all days for a month-range query
        returns 20–30 entries you will likely not need to read.

    ARGS:
      level: Granularity level. One of "day", "week", "month", or "year".

    RETURNS:
      JSON object with `level`, `total_files`, and `files` (sorted list of period start
      strings). Pass these exact strings to get_summary's `target_dates`.
      Example for level="month": ["2021-03", "2021-05", "2021-06"]
    """
    if level not in ("day", "week", "month", "year"):
        return f"ERROR: Invalid level '{level}'."

    try:
        conn = _get_db()
    except RuntimeError as e:
        return f"ERROR: {e}"

    try:
        periods = db_list_summaries(conn, level)
    except DatabaseError as e:
        return f"ERROR: Could not list summaries: {e}"
    
    if not periods:
        return f"No summaries found at the '{level}' level."
        
    return json.dumps({
        "level": level,
        "total_files": len(periods),
        "files": periods
    }, indent=2)

@mcp.tool()
def list_raw_files() -> str:
    """
    Lists all available raw entry dates — one per diary day.

    WHEN TO USE:
      - Always call this before get_raw_entry to verify a date has raw logs.
      - When the user asks "what days did we talk?", "do you have logs from
        [specific date]?", or when you need to know the date coverage of raw data.

    DO NOT call get_raw_entry without checking this list first — raw entries are
    large and a "not_found" wastes significant context.

    RETURNS:
      JSON object with `total_files` and `files` (sorted list of "YYYY-MM-DD"
      date strings). Use these exact strings in get_raw_entry's `target_dates`.
    """
    try:
        conn = _get_db()
    except RuntimeError as e:
        return f"ERROR: {e}"

    try:
        cursor = conn.execute("SELECT DISTINCT date FROM raw_entry ORDER BY date ASC")
        dates = [row[0] for row in cursor.fetchall()]
    except (sqlite3.Error, DatabaseError) as e:
        return f"ERROR: Could not list raw entry dates: {e}"

    if not dates:
        return "No raw entries found."
        
    return json.dumps({
        "total_files": len(dates),
        "files": dates
    }, indent=2)

@mcp.tool()
def get_raw_entry(target_dates: list[str]) -> str:
    """
    Retrieves the verbatim, unprocessed chat logs for specific days — the deepest dive available.

    WHEN TO USE:
      - For DAY-SCOPE queries: always read the raw entry for the target day —
        it is the primary source for any question about a specific date.
      - For WEEK-SCOPE queries: read raw entries for 1–2 days that stand out
        based on key_events or direct relevance to the user's question.
      - When the user explicitly asks for exact words: "what exactly did I say
        about X?", "can you show me what I wrote on [date]?"
      - When a day summary hints at a pivotal event that cannot be adequately
        described from the summary narrative alone.

    DO NOT USE for broad or exploratory queries — always read summaries first
    and drill to a specific date before fetching raw entries. Raw entries are
    large; fetching many at once will exhaust context rapidly.
    DO NOT call without first verifying the date exists via list_raw_files().

    ARGS:
      target_dates: List of date strings to fetch (batch multiple dates in one call).
                    Example: get_raw_entry(["2024-11-08", "2024-11-09"])

    RETURNS:
      JSON array — one entry per requested date. Each entry has `status` ("success"
      or "not_found") and either `data` (full chat log) or `smart_fallback_hint`
      (nearest available dates). If "not_found", use the fallback hint to retry.
    """
    try:
        conn = _get_db()
    except RuntimeError as e:
        return f"ERROR: {e}"

    try:
        cursor = conn.execute("SELECT DISTINCT date FROM raw_entry ORDER BY date ASC")
        available_dates = [row[0] for row in cursor.fetchall()]
    except (sqlite3.Error, DatabaseError) as e:
        return f"ERROR: Could not query raw entries: {e}"

    responses = []
    
    for query_date in target_dates:
        
        entries = get_raw_entries_by_date(conn, query_date)
        if entries:
            entry_dicts = [
                {
                    "time": e.time,
                    "source": e.source,
                    "source_file": e.source_file,
                    "role": e.role,
                    "text": e.text
                }
                for e in entries
            ]
            responses.append({
                "request": query_date,
                "status": "success",
                "data": {
                    "date": query_date,
                    "entries": entry_dicts
                }
            })
        else:
            prev_f, next_f = get_closest_files(query_date, available_dates)
            msg = f"No raw entry chat log found for '{query_date}'."
            if prev_f or next_f:
                msg += f" Closest available dates are: PREVIOUS=({prev_f or 'None'}), NEXT=({next_f or 'None'})."
            else:
                msg += " No raw entries exist."

            responses.append({
                "request": query_date,
                "status": "not_found",
                "smart_fallback_hint": msg
            })

    return json.dumps(responses, indent=2)


@mcp.tool()
def search_full_text(
    query: str,
    scope: str = "user",
    fuzzy_threshold: int = 80,
    context_chars: int = 150,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
) -> str:
    """
    Full-text search across all diary data using the database's search index — like Ctrl+F for the entire dataset.

    WHEN TO USE:
      - When the user asks "did I ever mention X?", "have I talked about Y before?",
        "when did I first bring up Z?"
      - When looking for a topic, place, concept, or name that might not appear
        in structured summary tags or event lists.
      - Use scope="user" (the default) when the user asks what THEY said or felt —
        this filters out AI assistant replies and returns only the user's own words.
      - Use scope="summaries" for faster, lower-noise results when exact phrasing
        is less important than thematic coverage.

    DO NOT USE to retrieve a full day or period — use get_summary or get_raw_entry.
    DO NOT USE to look up a person's relationship arc — use get_entity_profile.
    USE ONLY FOR SINGLE WORD SEARCHES. Correct usage: "vacation". Incorrect usage: "summer vacation cottage"

    ARGS:
      query:           Word or short phrase to search for (e.g. "burnout", "alumni").
      scope:           What corpus to search. Choose based on the user's intent:
                         "user"      — (default) raw entries, user's words ONLY;
                                       best for "did I ever say/feel/think X?"
                         "all"       — raw entries + all summary levels; broadest.
                         "raw"       — verbatim raw chat logs (user + AI replies).
                         "summaries" — all summary levels only (faster, less noise).
                         "day" | "week" | "month" | "year" — one summary level only.
      fuzzy_threshold: Match sensitivity, 0–100 (default 80). Note: FTS5-based search
                       uses stemmer matching (e.g. "running" matches "run") — this
                       parameter is accepted for backward compatibility but not applied.
      context_chars:   Characters of surrounding text shown on each side of a match.
                       Increase to 300+ when you need more surrounding context.
      date_from:       "YYYY-MM-DD" — restrict search to files on or after this date.
      date_to:         "YYYY-MM-DD" — restrict search to files on or before this date.
      limit:           Max results to return (default 50). Raise if needed; lower
                       to reduce context consumption when results are too many.

    RETURNS:
      JSON with `total_hits`, `files_scanned`, `truncated` flag, and a `results`
      array. Each result contains: `matched_word`, `score`, `level`, `date`,
      and `context` (the surrounding text snippet with the match highlighted as
      >>word<<). If `truncated` is true, narrow the search with date_from/date_to
      or use a more specific query.
    """
    try:
        conn = _get_db()
    except RuntimeError as e:
        return json.dumps({"error": str(e)})

    # Build FTS5 query with prefix matching
    fts_query = " OR ".join(f"{word}*" for word in query.strip().split())
    if not fts_query:
        return json.dumps({"query": query, "scope": scope, "total_hits": 0, "files_scanned": 0, "results": []})

    # Build source_type filter based on scope
    scope_conditions = []
    if scope == "user":
        # Find raw entries where role == 'user' using LIKE (works on plain tables)
        like_pattern = f"%{query}%"
        try:
            cursor = conn.execute("""
                SELECT re.date AS entry_date, re.text, re.role
                FROM raw_entry re
                WHERE re.role = 'user' AND re.text LIKE ?
                ORDER BY re.date ASC
                LIMIT ?
            """, (like_pattern, limit))
            raw_hits = cursor.fetchall()
        except sqlite3.Error:
            return json.dumps({"error": "FTS5 index not found. Run rebuild_fts_index() first."})

        results = []
        for row in raw_hits:
            date_str, text, role = row
            matched_lower = query.lower()
            idx = text.lower().find(matched_lower)
            if idx >= 0:
                start = max(0, idx - context_chars)
                end = min(len(text), idx + len(query) + context_chars)
                before = " ".join(text[start:idx].split())
                after = " ".join(text[idx + len(query):end].split())
                matched_word = text[idx:idx + len(query)]
                results.append({
                    "matched_word": matched_word,
                    "score": 100,
                    "level": "raw",
                    "date": date_str,
                    "context": f"...{before} >>{matched_word}<< {after}..."
                })

        total = len(results)
        truncated = total > limit
        displayed = results[:limit]
        result = {
            "query": query, "scope": scope, "fuzzy_threshold": fuzzy_threshold,
            "date_from": date_from, "date_to": date_to, "files_scanned": total,
            "total_hits": total, "results_shown": len(displayed), "truncated": truncated,
            "results": displayed,
        }
        if truncated:
            result["note"] = f"Results truncated to {limit}. Use date_from/date_to or a more specific query to narrow."
        return json.dumps(result, indent=2)

    # For non-"user" scopes, use FTS5 content_fts table
    source_type_map = {
        "raw": ("raw",),
        "summaries": ("summary_day", "summary_week", "summary_month", "summary_year"),
        "day": ("summary_day",),
        "week": ("summary_week",),
        "month": ("summary_month",),
        "year": ("summary_year",),
        "all": ("raw", "summary_day", "summary_week", "summary_month", "summary_year"),
    }

    if scope not in source_type_map:
        return json.dumps({"error": f"Unknown scope '{scope}'.", "valid_scopes": sorted(source_type_map.keys())})

    source_types = source_type_map[scope]
    placeholders = ",".join("?" for _ in source_types)

    fts_conn = conn.execute("PRAGMA database_list")
    try:
        params = list(source_types) + [fts_query, limit]
        fts_sql = f"""
            SELECT source_type, source_id, date, snippet(content_fts, 0, '>>', '<<', '...', 64)
            FROM content_fts
            WHERE source_type IN ({placeholders}) AND content_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        cursor = conn.execute(fts_sql, params)
        rows = cursor.fetchall()
    except sqlite3.OperationalError as e:
        return json.dumps({"error": f"FTS5 search failed: {e}"})

    results = []
    for source_type, source_id, date_str, snippet_text in rows:
        level_label = "raw" if source_type == "raw" else source_type.replace("summary_", "")

        results.append({
            "matched_word": query,
            "score": 100,
            "level": level_label,
            "date": date_str,
            "context": snippet_text
        })

    total = len(results)
    truncated = total > limit
    displayed = results[:limit]
    result = {
        "query": query, "scope": scope, "fuzzy_threshold": fuzzy_threshold,
        "date_from": date_from, "date_to": date_to, "files_scanned": total,
        "total_hits": total, "results_shown": len(displayed), "truncated": truncated,
        "results": displayed,
    }
    if truncated:
        result["note"] = f"Results truncated to {limit}. Use date_from/date_to or a more specific query to narrow."
    return json.dumps(result, indent=2)


# @mcp.tool()  # TEMPORARILY DISABLED to prevent LLM confusion
def _get_summary_dict(conn: sqlite3.Connection, level: str, period_start: str) -> dict | None:
    """Get a summary dict from the DB by level and period start."""
    summary = db_get_summary(conn, level, period_start)
    if summary is None:
        return None
    return summary.to_dict()

def search_timeline(query: str, level: str = "month") -> str:
    """
    Scan chronological summaries at a specific level for substring matches in KEY_EVENTS.
    Args:
        query: Substring keyword (e.g. "burnout", "vacation"). Case insensitive.
        level: "day", "week", "month", or "year". Defaults to "month".
    """
    if level not in ("day", "week", "month", "year"):
        return f"ERROR: Invalid level '{level}'."

    try:
        conn = _get_db()
    except RuntimeError as e:
        return f"ERROR: {e}"

    periods = db_list_summaries(conn, level)
    query_lower = query.lower()
    matches = []

    for period_start in periods:
        summary = _get_summary_dict(conn, level, period_start)
        if summary is None:
            continue

        events = summary.get("key_events") or summary.get("KEY_EVENTS") or []
        event_text = ""
        if isinstance(events, list):
            event_text = " ".join([
                e.get("description", str(e)) if isinstance(e, dict) else str(e)
                for e in events
            ]).lower()

        if query_lower in event_text:
            matches.append({
                "file": f"{period_start}.json",
                "matched_events": events
            })

    if not matches:
        return f"No matches found across '{level}' summaries containing the keyword '{query}'."

    return json.dumps({
        "query": query,
        "level": level,
        "total_matches": len(matches),
        "results": matches
    }, indent=2)

# @mcp.tool()  # TEMPORARILY DISABLED to prevent LLM confusion
def scan_scalar_metrics(level: str = "week") -> str:
    """
    Retrieves scalar trends (Energy Level, Social Connectedness, Forward Momentum) over time for a given level.
    Great for assessing overall well-being vectors without needing heavy summary contexts.
    """
    if level not in ("day", "week", "month", "year"):
        return f"ERROR: Invalid level '{level}'."

    try:
        conn = _get_db()
    except RuntimeError as e:
        return f"ERROR: {e}"

    periods = db_list_summaries(conn, level)
    results = []

    for period_start in periods:
        summary = _get_summary_dict(conn, level, period_start)
        if summary is None:
            continue
        metrics = summary.get("scalar_metrics") or summary.get("SCALAR_METRICS") or {}
        results.append({
            "file": f"{period_start}.json",
            "metrics": metrics
        })

    return json.dumps(results, indent=2)


# @mcp.tool()  # TEMPORARILY DISABLED to prevent LLM confusion
def scan_narrative_threads(level: str = "month") -> str:
    """
    Scans NARRATIVE_THREADS across summary files at a specific level to map open/closed life arcs.
    Especially useful to find out when a long-running thread started or resolved.
    Args:
        level: "day", "week", "month", or "year". Defaults to "month".
    """
    if level not in ("day", "week", "month", "year"):
        return f"ERROR: Invalid level '{level}'."

    try:
        conn = _get_db()
    except RuntimeError as e:
        return f"ERROR: {e}"

    periods = db_list_summaries(conn, level)
    all_threads = []

    for period_start in periods:
        summary = _get_summary_dict(conn, level, period_start)
        if summary is None:
            continue
        threads = summary.get("narrative_threads") or summary.get("NARRATIVE_THREADS") or []
        for thread in threads:
            if isinstance(thread, dict):
                all_threads.append({
                    **thread,
                    "found_in_file": f"{period_start}.json"
                })

    if not all_threads:
        return f"No narrative threads found across '{level}' summaries."

    return json.dumps({
        "level": level,
        "total_thread_entries": len(all_threads),
        "threads": all_threads
    }, indent=2)


if __name__ == "__main__":
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings
    
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
        allowed_hosts=["*"],
        allowed_origins=["*"]
    )
    mcp.settings.stateless_http = True  # match Open WebUI's connect-per-request pattern
    
    print("[Diary MCP] Starting Streamable HTTP server on http://0.0.0.0:8008/mcp ...")
    uvicorn.run(
        mcp.streamable_http_app(),
        host="0.0.0.0",
        port=8008,
    )