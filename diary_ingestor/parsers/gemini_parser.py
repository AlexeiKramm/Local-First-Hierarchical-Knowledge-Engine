"""
gemini_parser.py
================
Step 1: Parse MyActivity.html → list of {datetime_raw, datetime_parsed, user, response}
Step 2: Classify messages via LLM (local or OpenRouter) with checkpoint save-state

Output entries for assembler: {date, time, source, source_file, role, text, category}
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

LogCallback = Callable[[str], None]

FOOTER_SIGNAL = "This activity was saved to your Google Account"

DEFAULT_CATEGORIES = [
    "self_reflection_psychology_therapy_dating",
    "other_personal",
    "coding_related",
    "other",
]

_CLASSIFY_SYSTEM = """\
You are a message classifier. You will be given a conversation snippet (user message + AI response).
Classify it into EXACTLY ONE of these categories:
{categories}

Rules:
- Reply with ONLY the category name, nothing else.
- No punctuation, no explanation, no extra words.
- If uncertain, use "other".
"""

_CLASSIFY_USER = """\
USER MESSAGE:
{user}

AI RESPONSE (excerpt):
{response_preview}
"""


# ── Step 1: HTML Parsing ──────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parse_datetime(raw: str) -> Optional[str]:
    raw_no_tz = re.sub(r"\s+[A-Z]{2,5}$", "", raw.strip()).strip()
    for fmt in [
        "%b %d, %Y, %I:%M:%S %p", "%b %d, %Y, %H:%M:%S",
        "%B %d, %Y, %I:%M:%S %p", "%B %d, %Y, %H:%M:%S",
    ]:
        try:
            return datetime.strptime(raw_no_tz, fmt).isoformat()
        except ValueError:
            continue
    return None


def _extract_response_bs4(cell, datetime_raw: str) -> Optional[str]:
    raw_html = str(cell)
    ts_escaped = re.escape(datetime_raw[:10])
    ts_pos = re.search(ts_escaped, raw_html)
    if ts_pos is None:
        return None
    frag = BeautifulSoup(raw_html[ts_pos.end():], "html.parser")
    parts = [
        tag.get_text(" ", strip=True)
        for tag in frag.find_all(["p", "h3", "li"])
        if tag.get_text(" ", strip=True) and FOOTER_SIGNAL not in tag.get_text()
    ]
    return " ".join(parts) if parts else None


def _extract_fields(full_text: str, cell=None) -> Optional[dict]:
    footer_idx = full_text.find(FOOTER_SIGNAL)
    if footer_idx != -1:
        full_text = full_text[:footer_idx]

    prompted_idx = full_text.find("Prompted")
    if prompted_idx == -1:
        return None
    after = full_text[prompted_idx + len("Prompted"):].strip()

    was_used_m = re.search(
        r"\s+was used in this chat\.\s+Manage your Gems\.\s*", after, re.DOTALL
    )

    if not was_used_m:
        ts_m = re.search(
            r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
            r"\s+\d{1,2},\s+\d{4},\s+\d{1,2}:\d{2}:\d{2}\s+(?:AM|PM)(?:\s+[A-Z]{2,5})?)",
            after,
        )
        if not ts_m:
            return None
        return {
            "datetime_raw": ts_m.group(1).strip(),
            "datetime_parsed": _parse_datetime(ts_m.group(1).strip()),
            "user": _clean_text(after[: ts_m.start()]),
            "response": _clean_text(after[ts_m.end():]),
        }

    before_was = after[: was_used_m.start()]
    after_was  = after[was_used_m.end():]

    ts_pat = re.compile(
        r"^((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+\d{1,2},\s+\d{4},\s+\d{1,2}:\d{2}:\d{2}\s+(?:AM|PM)(?:\s+[A-Z]{2,5})?)\s*"
    )
    ts_m = ts_pat.match(after_was.strip())
    datetime_raw = ts_m.group(1).strip() if ts_m else ""
    response_raw = after_was.strip()[ts_m.end():].strip() if ts_m else after_was.strip()

    last_period = before_was.rfind(". ")
    if last_period != -1 and last_period > len(before_was) * 0.3:
        user_raw = before_was[: last_period + 1].strip()
    else:
        user_raw = before_was.strip()

    if cell is not None and BS4_AVAILABLE:
        response_raw = _extract_response_bs4(cell, datetime_raw) or response_raw

    return {
        "datetime_raw": datetime_raw,
        "datetime_parsed": _parse_datetime(datetime_raw),
        "user": _clean_text(user_raw),
        "response": _clean_text(response_raw),
    }


def _parse_with_bs4(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    messages = []
    for outer in soup.find_all("div", class_=lambda c: c and "outer-cell" in c):
        cell = outer.find(
            "div",
            class_=lambda c: c and "content-cell" in c and "mdl-typography--body-1" in c,
        )
        if cell is None:
            continue
        full_text = cell.get_text(" ", strip=False)
        if "Prompted" not in full_text:
            continue
        msg = _extract_fields(full_text, cell)
        if msg:
            messages.append(msg)
    return messages


def _parse_with_regex(html: str) -> list[dict]:
    text = re.sub(r"<[^>]+>", " ", html)
    for ent, rep in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                     ("&gt;", ">"), ("&#39;", "'"), ("&quot;", '"')]:
        text = text.replace(ent, rep)
    text = re.sub(r"\s+", " ", text)
    messages = []
    for chunk in re.split(r"Prompted", text)[1:]:
        footer_idx = chunk.find(FOOTER_SIGNAL)
        if footer_idx != -1:
            chunk = chunk[:footer_idx]
        msg = _extract_fields("Prompted" + chunk)
        if msg:
            messages.append(msg)
    return messages


def parse_gemini_html(filepath: str, log: Optional[LogCallback] = None) -> list[dict]:
    """Parse MyActivity.html -> list of message dicts."""
    _log = log or (lambda m: None)
    with open(filepath, "r", encoding="utf-8") as f:
        html = f.read()
    messages = _parse_with_bs4(html) if BS4_AVAILABLE else _parse_with_regex(html)
    _log(f"  Parsed {len(messages)} messages from {Path(filepath).name}")
    return messages



# ── Step 2: Classification ────────────────────────────────────────────────

def _match_category(raw: str, categories: list[str]) -> str:
    """
    Robustly map a raw LLM response string to one of the known categories.

    Strategy (strictest to loosest):
    1. Exact match after normalising non-alphanumeric chars to '_'.
    2. If the normalised raw equals a single complete underscore-token that
       uniquely identifies one category, use that category.
    3. Fall back to the "other" category (or the last category if "other"
       is not in the list).

    The previous code used `c.lower() in raw` and `raw in c.lower()` which
    caused substring false-positives, e.g. the LLM returning "other" would
    match "other_personal" because "other" is a prefix of that name.
    """
    raw_norm = re.sub(r"[^a-z0-9_]", "_", raw.lower()).strip("_")

    # 1. Exact match
    for c in categories:
        if c.lower() == raw_norm:
            return c

    # 2. Unique single-token match — the raw response is one underscore-segment
    #    that appears in exactly one category (and only that category).
    if raw_norm:
        matches = [
            c for c in categories
            if raw_norm in c.lower().split("_")
        ]
        if len(matches) == 1:
            return matches[0]

    # 3. Fallback to "other"
    return next((c for c in categories if c.lower() == "other"), categories[-1])


def classify_messages(
    messages: list[dict],
    categories: list[str],
    llm_client,
    checkpoint: dict[int, str],
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    on_checkpoint: Optional[Callable[[dict[int, str]], None]] = None,
    stop_event=None,
) -> dict[int, str]:
    """
    Classify messages. Skips entries already in checkpoint.
    Returns updated checkpoint dict {index: category}.

    on_progress(current, total, category) -- called after each classification
    on_checkpoint(checkpoint) -- called periodically to persist state
    stop_event -- threading.Event; if set, stops processing
    """
    system_prompt = _CLASSIFY_SYSTEM.format(
        categories="\n".join(f"- {c}" for c in categories)
    )
    total = len(messages)
    result = dict(checkpoint)  # make a copy to build on

    for i, msg in enumerate(messages):
        if stop_event and stop_event.is_set():
            break
        if i in result:
            if on_progress:
                on_progress(i + 1, total, result[i])
            continue

        user_content = _CLASSIFY_USER.format(
            user=msg.get("user", "")[:600],
            response_preview=msg.get("response", "")[:400],
        )
        try:
            raw = llm_client.complete(system_prompt, user_content, max_tokens=20, temperature=0.0)
            cat = _match_category(raw, categories)
        except Exception:
            cat = next((c for c in categories if c.lower() == "other"), categories[-1])

        result[i] = cat
        if on_progress:
            on_progress(i + 1, total, cat)

        # Checkpoint every 10 classifications
        if on_checkpoint and (i + 1) % 10 == 0:
            on_checkpoint(result)

    # Final checkpoint save
    if on_checkpoint:
        on_checkpoint(result)

    return result


def to_assembler_entries(
    messages: list[dict],
    categories: dict[int, str],
    source_file: str,
    diary_categories: Optional[set[str]] = None,
) -> list[dict]:
    """
    Convert classified messages to assembler-compatible entry dicts.
    If diary_categories is set, only include messages in those categories.
    """
    entries = []
    name = Path(source_file).name
    for i, msg in enumerate(messages):
        cat = categories.get(i, "other")
        if diary_categories and cat not in diary_categories:
            continue

        dt_str = msg.get("datetime_parsed")
        if not dt_str:
            continue
        try:
            dt = datetime.fromisoformat(dt_str)
        except ValueError:
            continue

        date = dt.strftime("%Y-%m-%d")
        time = dt.strftime("%H:%M:%S")

        if msg.get("user"):
            entries.append({
                "date": date, "time": time,
                "source": "gemini", "source_file": name,
                "role": "user", "text": msg["user"].strip(),
                "category": cat,
            })
        if msg.get("response"):
            entries.append({
                "date": date, "time": time,
                "source": "gemini", "source_file": name,
                "role": "assistant", "text": msg["response"].strip(),
                "category": cat,
            })

    entries.sort(key=lambda e: (e["date"], e["time"]))
    return entries
