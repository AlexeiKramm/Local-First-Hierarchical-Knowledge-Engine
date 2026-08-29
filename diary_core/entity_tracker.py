"""
entity_tracker.py
=================
Full two-pass entity profile builder, built into the diary_core
package. Mirrors all functionality from useful_tools/entity_profile_builder.py.

Pass 1 â€” Discovery:
  Scans all day summaries, reads ENTITY_MENTIONS fields, and writes a
  draft entity_profiles/_index.json (alias map + entity list). The human
  then reviews and merges aliases before running Pass 2.

Pass 2 â€” Profile Generation:
  Reads the reviewed _index.json. For each entity it:
    1. Collects every ENTITY_MENTIONS snippet from day summaries.
    2. Enriches each appearance with raw diary entries from the day
       itself and Â±N adjacent days (configurable). Also attaches the
       day's emotional_tone and key_events for framing.
    3. Calls the LLM once per entity with the full enriched timeline.
    4. Saves entity_profiles/{id}.json.

Backward-compatible API:
  The original EntityTracker class (dossier-based) is retained so any
  existing callers continue to work unchanged.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .llm_client import LLMClient
from .token_estimator import estimate

from .schema import (
    EntityDossier,
    EntityEntry,
    EntityProfile,
    TimestampedKnowledge,
)
from .progress_manager import ProgressManager
from mcp_server.db import (
    DatabaseError,
    get_raw_entries_by_date,
    upsert_entity_profile,
)

logger = logging.getLogger(__name__)


class EntityTrackerError(DatabaseError):
    """Raised when entity tracker operations fail due to database errors."""
    pass


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Backward-compatible EntityTracker (dossier-based, original implementation)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class EntityTracker:
    """
    Compiles entity dossiers from saved day/week/month summaries.
    Also handles saving and loading dossiers.

    This class is retained for backward compatibility. For the full
    two-pass entity profile system, use run_pass1() and run_pass2().
    """

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.dossier_dir = self.output_dir / "entities"
        self.dossier_dir.mkdir(parents=True, exist_ok=True)

    def build_dossier_from_summaries(
        self,
        label: str,
        aliases: list[str],
        unit_level: str,
        progress: ProgressManager,
    ) -> EntityDossier:
        """
        Scan all saved summaries at the given unit level and collect
        entity mentions matching label or aliases into an EntityDossier.
        """
        summaries = progress.load_all_units(unit_level)
        dossier = EntityDossier(entity_label=label, aliases=aliases)
        search_keys = [label.lower()] + [a.lower() for a in aliases]

        for s in summaries:
            if not s.entities:
                continue
            for key, note in s.entities.items():
                if any(sk in key.lower() or key.lower() in sk for sk in search_keys):
                    if "not mentioned" not in note.lower():
                        dossier.entries.append(EntityEntry(
                            period_start=s.period_start,
                            period_end=s.period_end,
                            unit=s.unit,
                            note=note,
                        ))
                    break

        return dossier

    def save_dossier(self, dossier: EntityDossier) -> Path:
        """Save an EntityDossier to disk."""
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_"
                             for c in dossier.entity_label)
        path = self.dossier_dir / f"{safe_label}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dossier.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    def load_dossier(self, label: str) -> Optional[EntityDossier]:
        """Load a previously saved EntityDossier by label."""
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        path = self.dossier_dir / f"{safe_label}.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        dossier = EntityDossier(
            entity_label=data["entity_label"],
            aliases=data.get("aliases", []),
            synthesis=data.get("synthesis"),
        )
        for e in data.get("entries", []):
            dossier.entries.append(EntityEntry(**e))
        return dossier

    def list_dossiers(self) -> list[str]:
        """Return a list of saved dossier labels."""
        return [p.stem for p in self.dossier_dir.glob("*.json")]

    def format_timeline_text(self, dossier: EntityDossier) -> str:
        """Format a dossier's entries into a readable timeline string for synthesis."""
        if not dossier.entries:
            return "(No mentions found)"
        lines = []
        for e in dossier.entries:
            lines.append(f"[{e.period_start}] {e.note}")
        return "\n".join(lines)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Shared helpers
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _normalize_id(name: str) -> str:
    """Convert a name to a snake_case canonical ID."""
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _parse_entity_mentions_block(block: str) -> list[dict]:
    """
    Parse the raw ENTITY_MENTIONS text block from a SummaryUnit into a list
    of structured mention dicts. Returns [] if block is empty/None.

    Expected structure (each entry is a YAML-ish bullet):
      - name_as_written: ...
        normalized_name: ...
        valence: ...
        interaction_summary: ...
        source_date: YYYY-MM-DD
    """
    if not block or block.strip().lower() in ("none", ""):
        return []

    mentions = []

    # 1. Find all start indices to dynamically chunk the block (avoids newline dependency)
    matches = list(re.finditer(r"(?i)name_as_written\s*:", block))
    if not matches:
        return []

    parts = []
    for i in range(len(matches)):
        start = matches[i].start()
        end = matches[i+1].start() if i+1 < len(matches) else len(block)
        parts.append(block[start:end])

    fields = ["name_as_written", "normalized_name", "valence", "interaction_summary", "source_date"]
    lookahead_fields = "|".join(fields)

    for item in parts:
        mention = {}
        for field in fields:
            # Look ahead for ANY subsequent field key, allowing optional punctuation/newlines before it.
            # This is critical for SLMs that flatten YAML arrays into single semicolon-separated strings.
            lookahead = rf"(?:\s*[;\n\r,\-\(\)]*\s*(?:{lookahead_fields})\s*:|\Z)"
            pat = rf"(?i){field}\s*:\s*(.+?){lookahead}"

            m = re.search(pat, item, re.DOTALL)
            if m:
                val = m.group(1).strip().strip('"\'')
                # If wrapped in stray closing parenthesis from SLM flat-string (e.g. "(source_date: ...)")
                if val.endswith(')') and '(' not in val:
                    val = val.rstrip(')')
                # Sanitize value to remove trailing newlines/hyphens from SLM flattening
                if '\n' in val:
                    val = val.split('\n')[0].strip()
                if val.endswith('-'):
                    val = val[:-1].strip()
                mention[field] = val

        if "name_as_written" in mention:
            mentions.append(mention)

    return mentions


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Raw entry loading
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _load_raw_entries_from_db(db_conn: sqlite3.Connection, date_str: str) -> str:
    """
    Load and flatten the raw diary entries for a single YYYY-MM-DD from
    the database. Returns a plain-text block or empty string.
    """
    try:
        entries = get_raw_entries_by_date(db_conn, date_str)
    except DatabaseError:
        logger.warning("Could not load raw entries for %s from DB.", date_str)
        return ""

    if not entries:
        return ""

    lines: list[str] = []
    for e in entries:
        t = e.time or ""
        txt = (e.text or "").strip()
        if txt:
            lines.append(f"  [{t}] {txt}" if t else f"  {txt}")
    return "\n".join(lines)


def _build_raw_context_window(
    db_conn: sqlite3.Connection,
    center_date: str,
    n_before: int,
    n_after: int,
) -> str:
    """
    Return a formatted multi-day raw context block covering
    [center_date - n_before … center_date + n_after].
    Only includes days that have actual content.
    Reads raw entries from the database.
    """
    try:
        center = datetime.strptime(center_date, "%Y-%m-%d")
    except ValueError:
        return ""

    parts: list[str] = []
    for delta in range(-n_before, n_after + 1):
        d = (center + timedelta(days=delta)).strftime("%Y-%m-%d")
        raw = _load_raw_entries_from_db(db_conn, d)
        if raw:
            label = "day of mention" if delta == 0 else (
                f"{abs(delta)} day(s) before" if delta < 0 else f"{abs(delta)} day(s) after"
            )
            parts.append(f"=== Raw entries: {d} ({label}) ===\n{raw}")

    return "\n\n".join(parts)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  LLM call helper
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _call_llm(
    api_url: str, model: str, system_prompt: str,
    user_prompt: str, max_tokens: int = 3000, temperature: float = 0.5,
) -> str:
    """Simple, self-contained OpenAI-compatible chat call."""
    endpoint = api_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens":  max_tokens,
        "temperature": temperature,
        "stream":      False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Pass 2: Profile Generation â€” prompts
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_ENTITY_SYNTHESIS_SYSTEM = """\
You are simultaneously a thoughtful therapist, empathic biographer, and personal historian.
Your task is to synthesize a rich, nuanced portrait of one person from the point of view of
the diarist, using all available evidence: pre-extracted summary mentions AND the diarist's
unedited raw journal entries for the relevant days. Prioritize the raw entries as primary
evidence; the summary mentions are pre-labeled anchors to guide your attention."""

_ENTITY_SYNTHESIS_PROMPT = """\
You are building a persistent psychological and relational profile of "{entity}" as they \
appear in the diarist's life.

You have been given two complementary types of evidence for each time this person was mentioned:

  TYPE A â€” ENTITY_MENTIONS (pre-extracted, structured):
    Compact, model-labeled summaries of each interaction: the valence, a 1-2 sentence
    description, and the date. Use these as anchors and cross-references.

  TYPE B â€” RAW DIARY ENTRIES (primary source, verbatim):
    The diarist's unedited words for the day of the mention and the {n_before} days before
    and {n_after} days after. These contain the full emotional texture, exact language,
    subtext, and context that the summary mention necessarily compresses. This is your richest
    evidence â€” read it carefully and mine it for nuance that the summary skips.

â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
FULL CHRONOLOGICAL EVIDENCE RECORD FOR "{entity}"
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
{entity_timeline}
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

Based on ALL of the above evidence, write a deep, specific, and psychologically rich synthesis.
Be concrete â€” cite approximate dates when describing turning points or shifts. Avoid vague
generalities. Use the diarist's own language and imagery where it illuminates the relationship.

Format your response EXACTLY as follows (keep the section headers):

RELATIONSHIP_ARC:
[How did this relationship evolve over the full period? What were the key turning points,
and roughly when did they happen? Was there growth, rupture, repair, drift, or deepening?
Be specific and chronological.]

EMOTIONAL_PATTERN:
[What consistent emotional dynamics appear in interactions with this person? What triggers
warmth, tension, withdrawal, openness, or conflict in the diarist? How does the diarist tend
to behave after interactions with this person?]

KEY_MOMENTS:
- [YYYY-MM-DD approx]: [Provide a COMPREHENSIVE, highly detailed paragraph. Do NOT just write a single sentence. Explain the raw emotional nuance, what specifically happened, the subtext, what led up to the event, and *why* it mattered psychologically to the diarist. Pull heavily from the TYPE B raw entries to provide "meat" and texture.]
- [YYYY-MM-DD approx]: [Detail...]
- [YYYY-MM-DD approx]: [Detail...]
(3â€“6 moments; choose for psychological significance and depth, not just frequency)

VALENCE_TREND:
[Describe the overall arc of emotional valence. Did the relationship get warmer, more
fraught, more complex, or more distant? Reference specific time windows.]

WHAT_THE_DIARIST_REVEALS_INDIRECTLY:
[What does the diarist's language, avoidance, or emotional reactions around this person
reveal that isn't stated directly? What seems unspoken or defended against?]

UNRESOLVED_QUESTIONS:
- [Something psychologically unresolved, ambiguous, or unclear]
- [Another unresolved or open thread]

OVERALL_ASSESSMENT:
[3â€“4 sentences: the nature, quality, psychological significance, and impact of this
relationship across the full diary period covered.]
"""

# Prompt used to merge multiple partial arc summaries into one final arc.
_ARC_MERGE_SYSTEM = """\
You are a thoughtful biographer and personal historian.
You have been given several PARTIAL arc summaries of a person, each covering a subsection
of the diarist's diary chronologically. Your task is to weave them into a single,
coherent, unified arc that reads as one continuous narrative."""

_ARC_MERGE_PROMPT = """\
Below are {n_chunks} chronological partial arc summaries for "{entity}".
Each chunk is labelled with the date range it covers.
Combine them into a single unified synthesis using EXACTLY the same section headers.

{partial_arcs}

Write the final merged synthesis now. Keep it specific, chronological, and psychologically
rich. Do not just concatenate â€” genuinely integrate the arcs into one coherent narrative.

Format your response EXACTLY as follows (keep the section headers):

RELATIONSHIP_ARC:
[Merged chronological arc across the full period.]

EMOTIONAL_PATTERN:
[Consistent emotional dynamics synthesised across all chunks.]

KEY_MOMENTS:
- [YYYY-MM-DD approx]: [Detail...]
(3â€“6 most significant moments across the entire period)

VALENCE_TREND:
[Overall valence trajectory across the full period.]

WHAT_THE_DIARIST_REVEALS_INDIRECTLY:
[Synthesised indirect signals across the full timeline.]

UNRESOLVED_QUESTIONS:
- [Unresolved thread]

OVERALL_ASSESSMENT:
[3â€“4 sentences covering the full arc.]
"""


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Token budget helpers
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_SAFETY_MARGIN:   float = 0.05  # reserve 5% of budget for variance


def _build_chunks(
    timeline_blocks: list[str],
    context_window_tokens: int,
    max_tokens_output: int,
    n_before: int,
    n_after: int,
    display_name: str,
    chunk_overlap: int = 3,
) -> list[list[str]]:
    """
    Partition timeline_blocks into chunks that each fit within the token budget.
    Consecutive chunks share `chunk_overlap` entries at their boundary for continuity.

    Budget per chunk:
      budget = context_window_tokens
               - max_tokens_output          (reserved for model response)
               - tokens(system_prompt)
               - tokens(prompt_template_rendered_without_timeline)
               - safety_margin
    """
    # Render the prompt template without the timeline to measure its fixed overhead
    template_overhead = _ENTITY_SYNTHESIS_PROMPT.format(
        entity=display_name,
        entity_timeline="",
        n_before=n_before,
        n_after=n_after,
    )
    fixed_tokens = (
        estimate(_ENTITY_SYNTHESIS_SYSTEM).estimated_tokens
        + estimate(template_overhead).estimated_tokens
    )
    safety_reserve = int(context_window_tokens * _SAFETY_MARGIN)
    budget = context_window_tokens - max_tokens_output - fixed_tokens - safety_reserve
    budget = max(budget, 500)  # floor: always allow at least a small chunk

    chunks: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0

    for block in timeline_blocks:
        block_tokens = estimate(block).estimated_tokens
        if current and current_tokens + block_tokens > budget:
            # Flush current chunk
            chunks.append(current)
            # Start new chunk with overlap from tail of current
            overlap = current[-chunk_overlap:] if chunk_overlap > 0 else []
            current = list(overlap)
            current_tokens = sum(estimate(b).estimated_tokens for b in current)
        current.append(block)
        current_tokens += block_tokens

    if current:
        chunks.append(current)

    return chunks


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Pass 1: Entity Discovery
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def run_pass1(db_conn: sqlite3.Connection, output_dir: str, log: callable) -> Path:
    """
    Scan all day-level summaries for ENTITY_MENTIONS fields.
    Build and write data/entity_index.json. Returns the path to the file.

    db_conn:    Active SQLite database connection containing summaries.
    output_dir: The project root that contains the data/ directory.
                entity_index.json is written to output_dir/data/.
    """
    pm = ProgressManager(db_conn=db_conn)
    summaries = pm.load_all_units("day")
    log(f"  Loaded {len(summaries)} day summaries.")

    # entity_id â†’ {display_name, aliases_set, first_mentioned, appearances}
    registry: dict[str, dict] = {}

    for s in sorted(summaries, key=lambda x: x.period_start):
        block = s.entity_mentions
        mentions = _parse_entity_mentions_block(block)
        if not mentions:
            continue

        for m in mentions:
            raw_name = m.get("name_as_written", "").strip()
            norm     = m.get("normalized_name", "").strip()
            source   = m.get("source_date", s.period_start)

            if not raw_name:
                continue

            canonical_id = _normalize_id(norm) if norm else _normalize_id(raw_name)

            if canonical_id not in registry:
                registry[canonical_id] = {
                    "id": canonical_id,
                    "display_name": raw_name,
                    "aliases": set(),
                    "first_mentioned": source or s.period_start,
                    "appearances": 0,
                    "profile_file": f"{canonical_id}.json",
                }

            entry = registry[canonical_id]
            entry["aliases"].add(raw_name.lower())
            if norm.lower() != raw_name.lower():
                entry["aliases"].add(norm.lower())
            entry["appearances"] += 1
            if source < entry["first_mentioned"]:
                entry["first_mentioned"] = source

    # Build alias_map: every known alias â†’ canonical_id
    alias_map: dict[str, str] = {}
    entities_list: list[dict] = []
    for eid, info in sorted(registry.items(), key=lambda x: -x[1]["appearances"]):
        for alias in info["aliases"]:
            alias_map[alias] = eid
        entities_list.append({
            "id": eid,
            "display_name": info["display_name"],
            "role": "",           # fill in manually during review
            "first_mentioned": info["first_mentioned"],
            "appearances": info["appearances"],
            "profile_file": info["profile_file"],
        })

    index = {"alias_map": alias_map, "entities": entities_list}
    data_dir = Path(output_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    index_path = data_dir / "entity_index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    log(f"\n  Discovered {len(entities_list)} unique entities.")
    log(f"  Wrote: {index_path}")
    log("\n  â”€â”€â”€ TOP 20 BY APPEARANCE â”€â”€â”€")
    for e in entities_list[:20]:
        log(f"    {e['appearances']:>4}Ã—  {e['id']}  (first: {e['first_mentioned']})")
    log("\n  âœ” Pass 1 complete. Review entity_index.json, merge aliases, then run Pass 2.")
    return index_path


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Pass 2: Profile Generation â€” logic
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def run_pass2(
    db_conn: sqlite3.Connection,
    output_dir: str,
    api_url: str,
    model: str,
    log: callable,
    stop_event: threading.Event,
    n_before: int = 1,
    n_after: int = 1,
    max_tokens: int = 3000,
    context_window_tokens: int = 32_768,
    chunk_overlap: int = 3,
    target_ids: list[str] | None = None,
    concurrency: int = 1,
    api_key: str = "",
) -> None:
    """
    For each entity in entity_index.json, build a richly contextualised timeline
    (entity_mentions snippets + raw diary entries ±N days), call the LLM for
    arc synthesis, and save entity profiles to the database.
    """
    data_dir = Path(output_dir) / "data"
    index_path  = data_dir / "entity_index.json"

    if not index_path.exists():
        log("  âœ— entity_index.json not found. Run Pass 1 first.")
        return

    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)

    alias_map: dict[str, str] = index.get("alias_map", {})

    if not alias_map:
        log("  âœ— alias_map is empty. Run Pass 1 first.")
        return

    # Derive the work list purely from alias_map values â€” ignore the entities list.
    # This is robust to manual _index.json edits where entities was partially cleaned
    # up but alias_map still holds the ground truth of what is canonical.
    all_canonical_ids = sorted(set(alias_map.values()))
    if target_ids is not None:
        target_set = set(target_ids)
        all_canonical_ids = [cid for cid in all_canonical_ids if cid in target_set]
    log(f"  Processing {len(all_canonical_ids)} canonical entity ID(s) from alias_map.")

    # Build reverse map: canonical_id â†’ all aliases (surface forms) pointing to it
    reverse_alias: dict[str, list[str]] = {}
    for alias, cid in alias_map.items():
        reverse_alias.setdefault(cid, []).append(alias)

    pm = ProgressManager(db_conn=db_conn)
    summaries = pm.load_all_units("day")
    summaries.sort(key=lambda x: x.period_start)
    log(f"  Loaded {len(summaries)} day summaries.")

    log(f"  Raw entries from database (+/-{n_before}/{n_after} days)")

    for i, eid in enumerate(all_canonical_ids):
        if stop_event.is_set():
            log("  â¹ Stopped.")
            return

        aliases      = reverse_alias.get(eid, [eid])
        display_name = eid       # canonical ID is the authoritative display name

        log(f"\n  [{i+1}/{len(all_canonical_ids)}] Building profile: {eid}")
        log(f"    Aliases ({len(aliases)}): {', '.join(sorted(aliases))}")

        # â”€â”€ Collect all appearances â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        timestamped_knowledge: list[TimestampedKnowledge] = []
        timeline_blocks: list[str] = []

        for s in summaries:
            mentions = _parse_entity_mentions_block(s.entity_mentions)
            for m in mentions:
                raw_written = m.get("name_as_written", "").lower()
                norm_id     = m.get("normalized_name", "").lower()
                resolved    = (
                    alias_map.get(raw_written)
                    or alias_map.get(norm_id)
                    or _normalize_id(norm_id or raw_written)
                )
                if resolved != eid:
                    continue

                source_date      = m.get("source_date") or s.period_start
                interaction_text = m.get("interaction_summary", "(no summary)").strip()
                valence          = m.get("valence", "neutral")

                # Build TimestampedKnowledge (stored in the profile JSON)
                timestamped_knowledge.append(TimestampedKnowledge(
                    valid_from      = source_date,
                    source_entries  = [s.period_start],
                    content         = interaction_text,
                    emotional_valence = valence,
                    tags            = [valence] if valence else [],
                ))

                # Build the evidence block sent to the LLM
                # â”€â”€ TYPE A: structured mention snippet â”€â”€
                mention_block = (
                    f"â”€â”€ TYPE A  [{source_date}]  valence={valence} â”€â”€\n"
                    f"{interaction_text}"
                )

                # â”€â”€ Attach day summary framing fields â”€â”€
                framing_parts: list[str] = []
                if s.emotional_tone:
                    framing_parts.append(f"Day emotional tone: {s.emotional_tone}")
                if s.key_events:
                    events_str = "\n".join(
                        f"  â€¢ {e}" for e in (s.key_events or [])[:5]
                    )
                    framing_parts.append(f"Key events that day:\n{events_str}")
                if framing_parts:
                    mention_block += "\n\n[Day framing]\n" + "\n".join(framing_parts)

                # â”€â”€ TYPE B: raw diary entries Â±N days from DB â”€â”€
                raw_context = _build_raw_context_window(
                    db_conn, source_date, n_before, n_after
                )
                if not raw_context:
                    logger.warning(
                        "No raw entries in DB for %s (center) +/-%d/%d days. "
                        "Entity context will be truncated.",
                        source_date, n_before, n_after,
                    )
                if raw_context:
                    mention_block += f"\n\nâ”€â”€ TYPE B  raw entries (Â±{n_before}/{n_after} days) â”€â”€\n{raw_context}"

                timeline_blocks.append(mention_block)

        mention_count = len(timestamped_knowledge)
        log(f"    Found {mention_count} mention(s).")

        # â”€â”€ LLM synthesis â€” dynamic chunked â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        arc_summary: str | None = None
        arc_chunks_used: int = 0

        if timestamped_knowledge:
            # Partition timeline_blocks into context-fitting chunks
            chunks = _build_chunks(
                timeline_blocks       = timeline_blocks,
                context_window_tokens = context_window_tokens,
                max_tokens_output     = max_tokens,
                n_before              = n_before,
                n_after               = n_after,
                display_name          = display_name,
                chunk_overlap         = chunk_overlap,
            )
            arc_chunks_used = len(chunks)

            if arc_chunks_used == 1:
                # Single chunk â€” standard path
                entity_timeline = "\n\n" + ("\n\n" + "â”€" * 60 + "\n\n").join(chunks[0])
                user_prompt = _ENTITY_SYNTHESIS_PROMPT.format(
                    entity          = display_name,
                    entity_timeline = entity_timeline,
                    n_before        = n_before,
                    n_after         = n_after,
                )
                log(f"    Single chunk ({len(user_prompt):,} chars). Calling LLMâ€¦")
                try:
                    t0 = time.time()
                    arc_summary = _call_llm(
                        api_url       = api_url,
                        model         = model,
                        system_prompt = _ENTITY_SYNTHESIS_SYSTEM,
                        user_prompt   = user_prompt,
                        max_tokens    = max_tokens,
                        temperature   = 0.5,
                    )
                    elapsed = round(time.time() - t0, 1)
                    log(f"    âœ“ Arc done ({elapsed}s, ~{len(arc_summary.split()):,} words).")
                except Exception as e:
                    log(f"    âœ— LLM call failed: {e}")
                    arc_summary = None
            else:
                # Multi-chunk path: synthesise each chunk, then merge
                log(f"    Timeline split into {arc_chunks_used} chunk(s) "
                    f"(context window: {context_window_tokens:,} tokens, "
                    f"overlap: {chunk_overlap} entries).")
                partial_arcs: list[str] = []

                for ci, chunk_blocks in enumerate(chunks):
                    if stop_event.is_set():
                        log("    â¹ Stopped during chunk synthesis.")
                        break

                    # Extract date range from this chunk's entries
                    chunk_dates = [
                        b.split("]")[0].lstrip("â”€ TYPE A  [").strip()
                        for b in chunk_blocks
                        if "TYPE A" in b
                    ]
                    date_range = (
                        f"{chunk_dates[0]} â†’ {chunk_dates[-1]}"
                        if chunk_dates else f"chunk {ci+1}"
                    )

                    entity_timeline = "\n\n" + ("\n\n" + "â”€" * 60 + "\n\n").join(chunk_blocks)
                    chunk_header = (
                        f"NOTE: This is chunk {ci+1} of {arc_chunks_used} "
                        f"covering entries {date_range}. "
                        f"Focus your analysis strictly on this date range."
                    )
                    user_prompt = _ENTITY_SYNTHESIS_PROMPT.format(
                        entity          = display_name,
                        entity_timeline = chunk_header + "\n\n" + entity_timeline,
                        n_before        = n_before,
                        n_after         = n_after,
                    )
                    log(f"    Chunk {ci+1}/{arc_chunks_used} [{date_range}] "
                        f"({len(user_prompt):,} chars)â€¦")
                    try:
                        t0 = time.time()
                        partial = _call_llm(
                            api_url       = api_url,
                            model         = model,
                            system_prompt = _ENTITY_SYNTHESIS_SYSTEM,
                            user_prompt   = user_prompt,
                            max_tokens    = max_tokens,
                            temperature   = 0.5,
                        )
                        elapsed = round(time.time() - t0, 1)
                        log(f"      âœ“ Chunk {ci+1} done ({elapsed}s).")
                        partial_arcs.append(
                            f"=== PARTIAL ARC â€” chunk {ci+1}/{arc_chunks_used} "
                            f"[{date_range}] ===\n{partial}"
                        )
                    except Exception as e:
                        log(f"      âœ— Chunk {ci+1} failed: {e}")

                # Merging pass
                if partial_arcs and not stop_event.is_set():
                    merge_prompt = _ARC_MERGE_PROMPT.format(
                        entity      = display_name,
                        n_chunks    = len(partial_arcs),
                        partial_arcs = "\n\n".join(partial_arcs),
                    )
                    log(f"    Merging {len(partial_arcs)} partial arcs "
                        f"({len(merge_prompt):,} chars)â€¦")
                    try:
                        t0 = time.time()
                        arc_summary = _call_llm(
                            api_url       = api_url,
                            model         = model,
                            system_prompt = _ARC_MERGE_SYSTEM,
                            user_prompt   = merge_prompt,
                            max_tokens    = max_tokens,
                            temperature   = 0.5,
                        )
                        elapsed = round(time.time() - t0, 1)
                        log(f"    âœ“ Merge done ({elapsed}s, "
                            f"~{len(arc_summary.split()):,} words).")
                    except Exception as e:
                        log(f"    âœ— Merge call failed: {e}")
                        # Fall back to concatenation of partial arcs
                        arc_summary = "\n\n".join(partial_arcs)
        else:
            log("    No mentions found â€” skipping LLM synthesis.")

        # â”€â”€ Save EntityProfile to database â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        first_mention = (
            min(tk.valid_from for tk in timestamped_knowledge)
            if timestamped_knowledge else ""
        )
        profile = EntityProfile(
            id                       = eid,
            display_name             = display_name,
            aliases                  = sorted(aliases),
            role_in_authors_life     = "",
            first_mentioned_in_diary = first_mention,
            stable_facts             = {"mention_count": mention_count,
                                        "arc_chunks_used": arc_chunks_used},
            timestamped_knowledge    = timestamped_knowledge,
            relationship_arc_summary = arc_summary,
            arc_last_updated         = datetime.now().strftime("%Y-%m-%d"),
        )
        try:
            upsert_entity_profile(db_conn, profile)
        except DatabaseError as e:
            log(f"    âœ— Failed to save profile to database: {e}")
            raise EntityTrackerError(str(e)) from e
        log(f"    Saved: {eid}  ({mention_count} mentions, "
            f"{arc_chunks_used} chunk(s))")


    index["last_pass2"] = datetime.now().isoformat(timespec="seconds")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    log("\n  âœ” Pass 2 complete.")
