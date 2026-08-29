"""
owui_parser.py
==============
Parses raw Open WebUI export JSON files.

Input format: a list of conversation objects, each with:
  root[].chat.messages[]   <- flat message list (preferred)
  root[].title             <- conversation title
  root[].chat.models[]     <- model(s) used

Output: list of entry dicts compatible with assembler.py
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

LogCallback = Callable[[str], None]

_REASONING_STRIP = re.compile(
    r'<details type="reasoning".*?</details>', re.DOTALL
)


def _strip_reasoning(text: str) -> str:
    """Remove OWUI reasoning blocks from assistant content."""
    return _REASONING_STRIP.sub("", text).strip()


def parse_owui_file(
    filepath: str,
    log: Optional[LogCallback] = None,
    include_titles: Optional[set[str]] = None,  # None = include all
) -> list[dict]:
    """
    Parse a raw OWUI export JSON and return a flat list of entry dicts:
      { date, time, source, source_file, role, text, conversation_title }

    include_titles: if provided, only conversations whose title is in this set
                    are included (for the GUI include/exclude filter).
    """
    _log = log or (lambda m: None)

    with open(filepath, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        _log(f"  ✗ Unexpected format in {os.path.basename(filepath)} — expected root list")
        return []

    entries: list[dict] = []
    source_name = Path(filepath).name
    conv_count = 0
    skipped_count = 0

    for conv in raw:
        title = conv.get("title", "(no title)")

        if include_titles is not None and title not in include_titles:
            skipped_count += 1
            continue

        # Prefer flat chat.messages[] array
        messages = []
        chat = conv.get("chat", {})
        if isinstance(chat, dict):
            messages = chat.get("messages", [])

        if not messages:
            skipped_count += 1
            continue

        conv_count += 1
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content", "")
            if not content:
                continue
            if role == "assistant":
                content = _strip_reasoning(content)
            if not content:
                continue

            timestamp = msg.get("timestamp")
            if timestamp is None:
                continue

            try:
                dt = datetime.fromtimestamp(int(timestamp))
            except (ValueError, OSError, OverflowError):
                continue

            entries.append({
                "date": dt.strftime("%Y-%m-%d"),
                "time": dt.strftime("%H:%M:%S"),
                "source": "owui",
                "source_file": source_name,
                "conversation_title": title,
                "role": role,
                "text": content.strip(),
            })

    _log(f"  ✓ OWUI: {conv_count} conversations → {len(entries)} messages (skipped {skipped_count})")

    # Sort chronologically
    entries.sort(key=lambda e: (e["date"], e["time"]))
    return entries


def list_conversations(filepath: str) -> list[dict]:
    """
    Quick scan returning [{"title": str, "message_count": int, "date_start": str}]
    for the GUI include/exclude list.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return []

    result = []
    for conv in (raw if isinstance(raw, list) else []):
        title = conv.get("title", "(no title)")
        messages = conv.get("chat", {}).get("messages", [])
        timestamps = [
            m.get("timestamp") for m in messages
            if isinstance(m, dict) and m.get("timestamp")
        ]
        date_start = ""
        if timestamps:
            try:
                date_start = datetime.fromtimestamp(min(timestamps)).strftime("%Y-%m-%d")
            except Exception:
                pass
        user_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "user"]
        result.append({
            "title": title,
            "message_count": len(user_messages),
            "date_start": date_start,
        })

    result.sort(key=lambda x: x["date_start"])
    return result
