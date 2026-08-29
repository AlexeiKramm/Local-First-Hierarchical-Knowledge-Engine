"""
preparsed_loader.py
===================
Loads a pre-parsed diary JSON file (Gemini cache format) and converts it
into the internal entry list format used by the OldDiaryTab Review pipeline.

Accepted format (list of dicts):
  [{
    "datetime_raw": "Jan 1, 2022, 10:00:00 AM EET",   # optional
    "datetime_parsed": "2022-01-01T10:00:00",           # required
    "user": "...",                                       # required
    "response": "..."                                    # optional, ignored
  }, ...]

Returns: list of {datetime_parsed, datetime_raw, user} — same as what
old_diary_parser.scan_file + build_entries produces.
"""
from __future__ import annotations

import json
from pathlib import Path


def load_preparsed_json(filepath: str) -> list[dict]:
    """
    Load a pre-parsed diary JSON file and return a list of entry dicts
    compatible with OldDiaryTab._entries.

    Raises ValueError with a descriptive message on bad input.
    """
    path = Path(filepath)
    if not path.exists():
        raise ValueError(f"File not found: {filepath}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(
            f"Expected a JSON array at the top level, got {type(data).__name__}.\n"
            "The file must be a list of {datetime_parsed, user, ...} objects."
        )

    entries = []
    skipped = 0
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            skipped += 1
            continue

        dt_parsed = item.get("datetime_parsed") or item.get("datetime_raw_parsed")
        user_text = item.get("user") or item.get("text") or ""

        if not dt_parsed or not user_text:
            skipped += 1
            continue

        entries.append({
            "datetime_parsed": dt_parsed,
            "datetime_raw": item.get("datetime_raw", dt_parsed),
            "user": user_text.strip(),
        })

    if not entries:
        raise ValueError(
            f"No valid entries found in file (checked {len(data)} items, "
            f"skipped {skipped}). Each item must have 'datetime_parsed' and 'user' fields."
        )

    # Sort chronologically
    entries.sort(key=lambda e: e.get("datetime_parsed", ""))
    return entries
