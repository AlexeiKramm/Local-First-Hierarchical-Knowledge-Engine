#!/usr/bin/env python3
"""
auto_ingest.py
==============
Frictionless diary ingestion CLI.

Usage:
    python auto_ingest.py
        Uses ingest_config.json for all settings.

    python auto_ingest.py --api-base http://localhost:8080 --model local-model
        CLI args override config file values.

    python auto_ingest.py --dry-run
        Print what would be done without writing anything.

Workflow:
    1. Scan raw_data/ for OWUI (.json), Gemini (.html), and Old Diary (.txt) files.
    2. Parse each source (OWUI and Gemini always; Old Diary only if mtime changed).
    3. For Gemini: only classify NEW messages not yet in the checkpoint.
    4. Assemble all entries into the database (incremental, deduped).
    5. Regenerate data/raw_data/processed/merged_diary.json.
    6. If new entries were found: run incremental summarization for affected dates.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Try loading .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

# ── Path constants ─────────────────────────────────────────────────────────

ROOT         = Path(__file__).resolve().parent   # project root
RAW_DATA     = ROOT / "data" / "raw_data"
CACHE_DIR    = RAW_DATA / "cache"
PROCESSED_DIR = RAW_DATA / "processed"
DIARY_DB     = ROOT / "data" / "diary.db"
MERGED_JSON  = PROCESSED_DIR / "merged_diary.json"
CONFIG_FILE  = ROOT / "config" / "ingest_config.json"

# Ensure directories exist
for _d in [CACHE_DIR, PROCESSED_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ── Add project root to sys.path ───────────────────────────────────────────
sys.path.insert(0, str(ROOT))


# ── Config loading ─────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load ingest_config.json with environment variable overrides. Returns defaults if missing."""
    defaults = {
        "diary_categories": ["personal_reflection", "work_and_projects", "daily_logs"],
        "ingestor": {
            "api_base": "http://localhost:8080",
            "model": "local-model",
            "api_key": "",
        },
        "analyzer": {
            "api_base": "https://openrouter.ai/api",
            "model": "deepseek/deepseek-v4-pro",
            "api_key": "",
        }
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # 1. Update diary_categories if present
            if "diary_categories" in data:
                defaults["diary_categories"] = data["diary_categories"]
                
            # 2. Check for dual-section format
            has_split = "ingestor" in data or "analyzer" in data
            
            if has_split:
                if "ingestor" in data and isinstance(data["ingestor"], dict):
                    defaults["ingestor"].update({k: v for k, v in data["ingestor"].items() if v is not None})
                if "analyzer" in data and isinstance(data["analyzer"], dict):
                    defaults["analyzer"].update({k: v for k, v in data["analyzer"].items() if v is not None})
            else:
                # Legacy flat format: map top-level keys to both sections
                flat_keys = {}
                for k in ["api_base", "model", "api_key"]:
                    if k in data and data[k]:
                        flat_keys[k] = data[k]
                
                defaults["ingestor"].update(flat_keys)
                defaults["analyzer"].update(flat_keys)
                
        except Exception as exc:
            print(f"[warn] Could not read {CONFIG_FILE.name}: {exc}")

    # Environment variable overrides
    env_ing_base = os.getenv("INGESTOR_API_BASE") or os.getenv("LLM_API_BASE")
    if env_ing_base:
        defaults["ingestor"]["api_base"] = env_ing_base
    env_ing_model = os.getenv("INGESTOR_MODEL") or os.getenv("LLM_MODEL")
    if env_ing_model:
        defaults["ingestor"]["model"] = env_ing_model
    env_ing_key = os.getenv("INGESTOR_API_KEY") or os.getenv("LLM_API_KEY")
    if env_ing_key:
        defaults["ingestor"]["api_key"] = env_ing_key

    env_anz_base = os.getenv("ANALYZER_API_BASE") or os.getenv("OPENROUTER_API_BASE") or os.getenv("LLM_API_BASE")
    if env_anz_base:
        defaults["analyzer"]["api_base"] = env_anz_base
    env_anz_model = os.getenv("ANALYZER_MODEL") or os.getenv("OPENROUTER_MODEL") or os.getenv("LLM_MODEL")
    if env_anz_model:
        defaults["analyzer"]["model"] = env_anz_model
    env_anz_key = os.getenv("ANALYZER_API_KEY") or os.getenv("OPENROUTER_API_KEY") or os.getenv("LLM_API_KEY")
    if env_anz_key:
        defaults["analyzer"]["api_key"] = env_anz_key

    env_cats = os.getenv("DIARY_CATEGORIES")
    if env_cats:
        defaults["diary_categories"] = [c.strip() for c in env_cats.split(",") if c.strip()]

    return defaults


# ── Argument parsing ───────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Frictionless diary ingestion — drop files in raw_data/, run this script."
    )
    p.add_argument("--api-base", help="Fallback/global LLM API base URL")
    p.add_argument("--model", help="Fallback/global Model name")
    p.add_argument("--api-key", help="Fallback/global API key")
    p.add_argument("--ingestor-api-base", help="Ingestor LLM API base URL")
    p.add_argument("--ingestor-model", help="Ingestor model name")
    p.add_argument("--ingestor-api-key", help="Ingestor API key")
    p.add_argument("--analyzer-api-base", help="Analyzer LLM API base URL")
    p.add_argument("--analyzer-model", help="Analyzer model name")
    p.add_argument("--analyzer-api-key", help="Analyzer API key")
  #  p.add_argument("--summaries-dir", help=f"Override summaries output directory (default: {SUMMARIES})", default=str(SUMMARIES),)
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be done without writing anything.",
    )
    p.add_argument(
        "--skip-analysis", action="store_true",
        help="Stop after assembly — skip the diary_core summarization step.",
    )
    p.add_argument(
        "--force-summarize", action="store_true",
        help="Force running summarization on all days even if no new entries were ingested.",
    )
    return p.parse_args()


# ── Logging ────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")





# ── Step 1: Scan raw_data/ ─────────────────────────────────────────────────

def scan_raw_data() -> dict[str, list[Path]]:
    """Return {'owui': [...], 'gemini': [...], 'old_diary': [...]}"""
    owui, gemini, old_diary = [], [], []
    excluded = {CACHE_DIR.resolve(), PROCESSED_DIR.resolve()}

    for f in sorted(RAW_DATA.iterdir()):
        if f.is_dir():
            continue
        if f.resolve().parent in excluded:
            continue
        if f.suffix.lower() == ".json":
            owui.append(f)
        elif f.suffix.lower() == ".html":
            gemini.append(f)
        elif f.suffix.lower() in (".txt", ".md"):
            old_diary.append(f)

    return {"owui": owui, "gemini": gemini, "old_diary": old_diary}


# ── Step 2a: OWUI parsing ──────────────────────────────────────────────────

def process_owui(files: list[Path], dry_run: bool) -> list[dict]:
    """Always parse all OWUI files. Assembler handles deduplication."""
    from diary_ingestor.parsers.owui_parser import parse_owui_file

    all_entries: list[dict] = []
    for f in files:
        log(f"  [OWUI] Parsing {f.name}…")
        if dry_run:
            log(f"    → (dry run) would parse {f.name}")
            continue
        try:
            entries = parse_owui_file(str(f), log=lambda m: print(f"    {m}"))
            log(f"    → {len(entries)} entries")
            all_entries.extend(entries)
        except Exception as exc:
            log(f"    ✗ Error: {exc}")
    return all_entries


# ── Step 2b: Gemini parsing ────────────────────────────────────────────────

def process_gemini(
    files: list[Path],
    llm_client,
    diary_categories: set[str],
    dry_run: bool,
) -> list[dict]:
    """
    Always parse HTML. Only classify messages not yet in the checkpoint.
    Returns assembler-compatible entries (diary categories only).
    """
    from diary_ingestor.parsers.gemini_parser import (
        parse_gemini_html,
        classify_messages,
        to_assembler_entries,
        DEFAULT_CATEGORIES,
    )
    from diary_ingestor.progress_store import ProgressStore

    store = ProgressStore(str(CACHE_DIR))
    all_entries: list[dict] = []

    for f in files:
        log(f"  [Gemini] Parsing {f.name}…")
        if dry_run:
            log(f"    → (dry run) would parse and classify {f.name}")
            continue

        try:
            messages = parse_gemini_html(str(f), log=lambda m: print(f"    {m}"))
            log(f"    → {len(messages)} messages parsed")
        except Exception as exc:
            log(f"    ✗ Parse error: {exc}")
            continue

        # Load existing classification checkpoint
        checkpoint = store.load_classify_checkpoint(str(f))
        new_count = sum(1 for i in range(len(messages)) if i not in checkpoint)
        log(f"    → {len(checkpoint)} already classified, {new_count} new to classify")

        if new_count > 0:
            # Use all categories for classification accuracy, filter to diary cats later
            cats = list(diary_categories) + [
                c for c in DEFAULT_CATEGORIES if c not in diary_categories
            ]

            def _on_checkpoint(cp: dict):
                store.save_classify_checkpoint(str(f), cp)

            def _on_progress(cur: int, tot: int, cat: str):
                if cur % 10 == 0 or cur == tot:
                    log(f"    Classified {cur}/{tot} — last: {cat}")

            checkpoint = classify_messages(
                messages=messages,
                categories=cats,
                llm_client=llm_client,
                checkpoint=checkpoint,
                on_progress=_on_progress,
                on_checkpoint=_on_checkpoint,
            )
            log(f"    ✓ Classification complete — {len(checkpoint)} total classified")

        entries = to_assembler_entries(
            messages=messages,
            categories=checkpoint,
            source_file=str(f),
            diary_categories=diary_categories,
        )
        log(f"    → {len(entries)} diary entries (after category filter)")
        all_entries.extend(entries)

    return all_entries


# ── Step 2c: Old Diary parsing ─────────────────────────────────────────────

def process_old_diary(
    files: list[Path],
    llm_client,
    dry_run: bool,
) -> list[dict]:
    """
    mtime-gated: only parses if the source file has changed since last ingestion.
    Uses LLM to detect date headers; checkpoint stored in raw_data/cache/.
    """
    from diary_ingestor.parsers.old_diary_parser import (
        scan_file,
        build_entries,
        resolve_undated,
        to_assembler_entries,
    )
    from diary_ingestor.progress_store import ProgressStore

    store = ProgressStore(str(CACHE_DIR))
    all_entries: list[dict] = []

    for f in files:
        log(f"  [OldDiary] Checking {f.name}…")

        # mtime gate — skip if file hasn't changed
        if store.is_processed(str(f)):
            log(f"    → Unchanged since last ingestion, skipping.")
            continue

        log(f"    → New or modified. Scanning for date headers…")
        if dry_run:
            log(f"    → (dry run) would scan {f.name}")
            continue

        prior = store.load_date_cache(str(f))
        if prior:
            log(f"    → Resuming from {len(prior)} cached anchors.")

        with open(f, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        log(f"    → {len(lines)} lines total, {len(prior)} already scanned.")

        def _on_checkpoint(anchors: list):
            store.save_date_cache(str(f), anchors)

        anchors = scan_file(
            str(f),
            llm_client,
            prior_checkpoint=prior,
            on_checkpoint=_on_checkpoint,
            log=lambda m: print(f"    {m}"),
        )

        entries_raw = build_entries(lines, anchors)
        entries_resolved = resolve_undated(entries_raw)
        entries = to_assembler_entries(entries_resolved, str(f))

        log(f"    → {len(anchors)} date anchors → {len(entries)} entries")
        all_entries.extend(entries)

        # Mark file as processed (records current mtime)
        store.mark_processed(str(f), source_type="old_diary", entries_added=len(entries))

    return all_entries


# ── Step 3: Assemble ───────────────────────────────────────────────────────

def assemble(all_entries: list[dict], dry_run: bool) -> set[str]:
    """
    Write entries to raw_entries/ (deduped, incremental).
    Regenerate merged_diary.json.
    Returns the set of date strings that received new entries.
    """
    from diary_ingestor.assembler import Assembler

    if dry_run:
        log(f"  (dry run) Would assemble {len(all_entries)} entries")
        log(f"  (dry run) Would regenerate {MERGED_JSON}")
        return set()

    assembler = Assembler(str(DIARY_DB), log=lambda m: print(f"  {m}"))
    result = assembler.assemble(all_entries)

    log(
        f"  Assembly complete:\n"
        f"    New date files:     {result['new_dates']}\n"
        f"    Entries appended:   {result['appended']}\n"
        f"    Duplicates skipped: {result['skipped_duplicates']}"
    )

    assembler.regenerate_merged(str(MERGED_JSON))
    log(f"  ✓ Merged JSON regenerated → {MERGED_JSON}")

    # Determine which dates received new entries
    # The assembler groups by date before writing; reconstruct from entries
    new_dates: set[str] = set()
    if result["appended"] > 0:
        # Re-read the assembler result by checking which dates had new entries.
        # We derive this by noting which date-keyed entries were in all_entries —
        # the assembler's dedup means only truly new ones were appended.
        for e in all_entries:
            d = e.get("date")
            if d:
                new_dates.add(d)
    return new_dates


# ── Step 4: Incremental summarization ────────────────────────────────────

def run_summarization(
    new_dates: set[str],
    llm_client,
    dry_run: bool,
):
    """
    Run diary_core only for the date range that has new entries.
    cascade_updates=True invalidates affected week/month/year summaries.
    """
    from diary_core.input_loader import load_from_db
    from diary_core.progress_manager import ProgressManager
    from diary_core.summarizer import SummarizationConfig
    from diary_core.hierarchy_builder import HierarchyBuilder
    from mcp_server.db import get_db_connection

    if not new_dates:
        if dry_run:
            log("  (dry run) No new dates to summarize.")
        else:
            log("  No new dates to summarize.")
        return

    sorted_dates = sorted(new_dates)
    target_range = (sorted_dates[0], sorted_dates[-1])
    log(f"  Target range: {target_range[0]} → {target_range[-1]}")

    if dry_run:
        log(f"  (dry run) Would run summarization for {len(new_dates)} date(s)")
        return

    log(f"  Loading entries from database…")
    conn = get_db_connection(DIARY_DB)
    try:
        days = load_from_db(conn)
    finally:
        conn.close()
    log(f"  Loaded {len(days)} days into memory.")

    run_cfg = SummarizationConfig(
        mode="historical",
        history_n=7,
        context_window=262_144,
        output_budget=16384,
        max_tokens=16384,
        temperature=0.8,
        output_format="section",
        concurrency=3,
        api_key=llm_client.api_key or "",
        inject_grandchild_data=False,   # Set to True to inject grandchild-level data into prompts
    )

    conn = get_db_connection(DIARY_DB)
    progress = ProgressManager(db_conn=conn)
    done = progress.status_summary()
    if done:
        log(f"  Existing summaries: {done}")

    builder = HierarchyBuilder(
        client=llm_client,
        progress=progress,
        config=run_cfg,
        log=log,
    )

    results = builder.run(
        days=days,
        levels=("day", "week", "month", "year"),
        target_range=target_range,
        cascade_updates=True,
    )

    log("\n✓ Summarization complete. Results by level:")
    for lvl, items in results.items():
        if isinstance(items, list):
            log(f"  {lvl}: {len(items)} units")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    cfg = load_config()

    # Determine Ingestor Config
    ing_cfg = cfg["ingestor"]
    ing_api_base = args.ingestor_api_base or args.api_base or ing_cfg["api_base"]
    ing_model    = args.ingestor_model    or args.model    or ing_cfg["model"]
    ing_api_key  = getattr(args, "ingestor_api_key", None) or args.api_key or ing_cfg.get("api_key", "")

    # Determine Analyzer Config
    anz_cfg = cfg["analyzer"]
    anz_api_base = args.analyzer_api_base or args.api_base or anz_cfg["api_base"]
    anz_model    = args.analyzer_model    or args.model    or anz_cfg["model"]
    anz_api_key  = getattr(args, "analyzer_api_key", None) or args.api_key or anz_cfg.get("api_key", "")

    diary_categories = set(cfg.get("diary_categories", ["self_reflection_psychology_therapy_dating"]))
    dry_run = args.dry_run
    skip_analysis = args.skip_analysis
    force_summarize = args.force_summarize

    if dry_run:
        log("=== DRY RUN MODE -- nothing will be written ===")

    log(f"Ingestor API base:  {ing_api_base}")
    log(f"Ingestor Model:     {ing_model}")
    log(f"Analyzer API base:  {anz_api_base}")
    log(f"Analyzer Model:     {anz_model}")
    log(f"Categories:         {sorted(diary_categories)}")

    # Build LLM client (needed for Gemini classification + Old Diary)
    from diary_ingestor.llm_client import LLMClient as IngestorLLMClient
    ingestor_client = IngestorLLMClient(
        api_base=ing_api_base,
        model_name=ing_model,
        api_key=ing_api_key or None,
    )

    # ── Step 1: Scan ──────────────────────────────────────────────────────
    log("\n=== STEP 1: SCAN raw_data/ ===")
    sources = scan_raw_data()
    log(f"  Found: {len(sources['owui'])} OWUI, {len(sources['gemini'])} Gemini, "
        f"{len(sources['old_diary'])} Old Diary file(s)")

    if not any(sources.values()):
        log("No source files found in raw_data/. Nothing to do.")
        return

    # ── Step 2: Parse ─────────────────────────────────────────────────────
    log("\n=== STEP 2: PARSE ===")
    all_entries: list[dict] = []

    if sources["owui"]:
        log(f"  Processing {len(sources['owui'])} OWUI file(s)…")
        all_entries.extend(process_owui(sources["owui"], dry_run))

    if sources["gemini"]:
        log(f"  Processing {len(sources['gemini'])} Gemini file(s)…")
        all_entries.extend(process_gemini(
            sources["gemini"], ingestor_client, diary_categories, dry_run
        ))

    if sources["old_diary"]:
        log(f"  Processing {len(sources['old_diary'])} Old Diary file(s)…")
        all_entries.extend(process_old_diary(
            sources["old_diary"], ingestor_client, dry_run
        ))

    log(f"\n  Total entries collected: {len(all_entries)}")

    # ── Step 3: Assemble ──────────────────────────────────────────────────
    log("\n=== STEP 3: ASSEMBLE ===")
    new_dates = assemble(all_entries, dry_run)

    if force_summarize and not new_dates:
        try:
            from diary_core.input_loader import load_from_db
            from mcp_server.db import get_db_connection
            conn = get_db_connection(DIARY_DB)
            try:
                days = load_from_db(conn)
            finally:
                conn.close()
            new_dates = {d.date for d in days}
            log(f"  --force-summarize: Loaded {len(new_dates)} date(s) from database.")
        except Exception as exc:
            log(f"  ✗ Failed to load entries from database for force-summarize: {exc}")

    if not new_dates and not dry_run:
        log("\n✓ No new entries found. Everything is up to date.")
        return

    # ── Step 4: Summarize ─────────────────────────────────────────────────
    if skip_analysis:
        log("\n--skip-analysis flag set. Skipping diary_core step.")
        log(f"  New/updated dates: {sorted(new_dates)}")
        return

    if new_dates or dry_run:
        log("\n=== STEP 4: INCREMENTAL SUMMARIZATION ===")

        # Build diary_core LLM client
        from diary_core.llm_client import LLMClient as AnalyzerLLMClient
        analyzer_client = AnalyzerLLMClient(
            api_base=anz_api_base,
            model_name=anz_model,
            api_key=anz_api_key or "",
        )

        run_summarization(
            new_dates=new_dates,
            llm_client=analyzer_client,
            dry_run=dry_run,
        )

    log("\n✓ auto_ingest complete.")


if __name__ == "__main__":
    main()
