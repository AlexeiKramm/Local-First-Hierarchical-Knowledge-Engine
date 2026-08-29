"""
old_diary_parser.py
===================
Parses legacy text/markdown diary files using an LLM to detect date headers.
Produces entries: {datetime_parsed, datetime_raw, user, response}
Supports checkpoint save-state to resume partial runs.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

LogCallback = Callable[[str], None]

_SYSTEM_PROMPT = """\
You are a date extraction and formatting assistant.
You will be given a text snippet from a diary entry (usually the beginning).
Your task is to extract the calendar date from the text and return it strictly in 'YYYY-MM-DD' format.

Rules:
- If there is a date, reply with ONLY the 'YYYY-MM-DD', nothing else.
- If there is NO date or the entry is clearly continuous text without a date header, reply with 'NONE'.
- No punctuation, no explanation, no extra words.
- If days or months are missing but year is present, use '01' as default (e.g., 'May 2022' -> '2022-05-01').
"""

_USER_TEMPLATE = """\
DIARY SNIPPET:
\"\"\"{snippet}\"\"\"

EXTRACTED DATE (YYYY-MM-DD or NONE):
"""


def _extract_date_with_llm(snippet: str, llm_client, timeout: int = 15) -> Optional[str]:
    try:
        content = llm_client.complete(
            _SYSTEM_PROMPT,
            _USER_TEMPLATE.format(snippet=snippet[:400]),
            max_tokens=15,
            temperature=0.0,
        )
        content = content.strip()
        if content.upper() == "NONE":
            return None
        if re.match(r"^\d{4}-\d{2}-\d{2}$", content):
            return content
        return None
    except Exception:
        return None


def scan_file(
    filepath: str,
    llm_client,
    prior_checkpoint: Optional[list[dict]] = None,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    on_checkpoint: Optional[Callable[[list[dict]], None]] = None,
    stop_event=None,
    log: Optional[LogCallback] = None,
) -> list[dict]:
    """
    Scan a text file line by line, using LLM to identify date headers.
    Returns list of {line_index, datetime_parsed, line_text} anchors + the grouped entries.

    If prior_checkpoint contains already-processed lines, resume from there.
    """
    _log = log or (lambda m: None)

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    total = len(lines)
    _log(f"  Scanning {total} lines for date headers…")

    # Build a set of already-known anchor line indices from prior checkpoint
    known_anchors: dict[int, Optional[str]] = {}
    if prior_checkpoint:
        for item in prior_checkpoint:
            known_anchors[item["line_index"]] = item.get("datetime_parsed")

    anchors: list[dict] = list(prior_checkpoint or [])

    for i, line in enumerate(lines):
        if stop_event and stop_event.is_set():
            break

        stripped = line.strip()
        if not stripped:
            continue

        if i in known_anchors:
            if on_progress:
                on_progress(i + 1, total, f"cached: {known_anchors[i] or 'NONE'}")
            continue

        date_str = _extract_date_with_llm(stripped, llm_client)
        if date_str:
            _log(f"    Line {i+1}: Header → {date_str} ('{stripped[:30]}…')")
            anchors.append({"line_index": i, "datetime_parsed": date_str, "line_text": stripped})
            known_anchors[i] = date_str
        else:
            known_anchors[i] = None

        if on_progress:
            on_progress(i + 1, total, date_str or "none")

        # Checkpoint every 50 lines
        if on_checkpoint and (i + 1) % 50 == 0:
            on_checkpoint(sorted(anchors, key=lambda x: x["line_index"]))

    if on_checkpoint:
        on_checkpoint(sorted(anchors, key=lambda x: x["line_index"]))

    return sorted(anchors, key=lambda x: x["line_index"])


def build_entries(lines: list[str], anchors: list[dict]) -> list[dict]:
    """
    Given raw file lines and detected anchor positions,
    group text between anchors into diary entries.
    """
    entries = []

    if not anchors:
        content = "".join(lines).strip()
        if content:
            entries.append({"datetime_parsed": None, "datetime_raw": "None", "user": content})
        return entries

    # Text before first anchor
    first_idx = anchors[0]["line_index"]
    if first_idx > 0:
        pre = "".join(lines[:first_idx]).strip()
        if pre:
            entries.append({"datetime_parsed": None, "datetime_raw": "Prior to first entry", "user": pre})

    for j, anchor in enumerate(anchors):
        start = anchor["line_index"]
        end = anchors[j + 1]["line_index"] if j + 1 < len(anchors) else len(lines)
        content = "".join(lines[start:end]).strip()
        entries.append({
            "datetime_parsed": f"{anchor['datetime_parsed']}T00:00:00" if anchor["datetime_parsed"] else None,
            "datetime_raw": lines[start].strip(),
            "user": content,
        })

    return entries


def resolve_undated(entries: list[dict]) -> list[dict]:
    """
    Assign undated entries to the day before the earliest dated entry.
    """
    dated = [e for e in entries if e.get("datetime_parsed")]
    undated = [e for e in entries if not e.get("datetime_parsed")]

    if not undated:
        return entries

    earliest = None
    if dated:
        dated.sort(key=lambda e: e["datetime_parsed"])
        try:
            earliest = datetime.strptime(dated[0]["datetime_parsed"][:10], "%Y-%m-%d")
        except ValueError:
            pass

    for e in undated:
        if earliest:
            target = (earliest - timedelta(days=1)).strftime("%Y-%m-%d")
            e["datetime_parsed"] = f"{target}T00:00:00"
            e["user"] += f"\n\n[Note: undated entry, placed before {earliest.strftime('%Y-%m-%d')}]"
        else:
            e["datetime_parsed"] = "1970-01-01T00:00:00"

    all_entries = dated + undated
    all_entries.sort(key=lambda e: e.get("datetime_parsed", ""))
    return all_entries


def to_cache_format(entries: list[dict]) -> list[dict]:
    """
    Convert parsed old diary entries to Gemini cache format:
    [{datetime_raw, datetime_parsed, user, response}]
    so they can be saved to disk and reloaded by preparsed_loader.
    """
    result = []
    for e in entries:
        result.append({
            "datetime_raw":    e.get("datetime_raw", ""),
            "datetime_parsed": e.get("datetime_parsed") or "",
            "user":            e.get("user", "").strip(),
            "response":        "",
        })
    return result


def to_assembler_entries(entries: list[dict], source_file: str) -> list[dict]:
    """Convert old diary entries to assembler-compatible format."""
    result = []
    name = Path(source_file).name
    for e in entries:
        dt_str = e.get("datetime_parsed")
        if not dt_str:
            continue
        try:
            dt = datetime.fromisoformat(dt_str)
        except ValueError:
            continue
        result.append({
            "date": dt.strftime("%Y-%m-%d"),
            "time": dt.strftime("%H:%M:%S"),
            "source": "old_diary",
            "source_file": name,
            "role": "user",
            "text": e.get("user", "").strip(),
        })
    return result
