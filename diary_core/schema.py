"""
schema.py
=========
Defines the canonical data structures for the diary analyzer.
All JSON summaries produced by the system conform to these dataclasses.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import json


# ─────────────────────────────────────────────
#  Entry-level schema (unified merger output)
# ─────────────────────────────────────────────

@dataclass
class RawEntry:
    """A single diary entry as produced by raw_data_merger.py or saved from a live session."""
    date: str                          # "YYYY-MM-DD"
    time: str                          # "HH:MM:SS"
    source: str                        # "gemini" | "owui" | "txt" | "md" | "csv" | "session"
    source_file: str
    text: str
    role: str = "user"                 # "user" | "assistant" — defaults to "user" for backward compat


@dataclass
class DayEntries:
    """A group of raw entries for a single day."""
    date: str
    entries: list[RawEntry]

    @property
    def combined_text(self) -> str:
        """
        Concatenate all entry texts into a single string with role labels.
        Format: [HH:MM - user]: text  /  [HH:MM - assistant]: text
        """
        parts = []
        for e in self.entries:
            label = e.role if e.role in ("user", "assistant") else "user"
            parts.append(f"[{e.time} - {label}]: {e.text}")
        return "\n\n".join(parts)

    def combined_text_filtered(self, roles: list[str] | None = None) -> str:
        """
        Same as combined_text but filtered to only the given roles.
        roles=None means all; roles=["user"] means only user entries, etc.
        """
        parts = []
        for e in self.entries:
            if roles is not None and e.role not in roles:
                continue
            label = e.role if e.role in ("user", "assistant") else "user"
            parts.append(f"[{e.time} - {label}]: {e.text}")
        return "\n\n".join(parts)


# ─────────────────────────────────────────────
#  Summary output schema
# ─────────────────────────────────────────────

@dataclass
class SummaryUnit:
    """
    Universal summary record. Applies at day / week / month / year level.
    Fields that are not yet populated are None.
    """
    unit: str                               # "day" | "week" | "month" | "year"
    period_start: str                       # "YYYY-MM-DD"
    period_end: str                         # "YYYY-MM-DD"

    # Core summary
    summary: Optional[str] = None
    emotional_tone: Optional[str] = None
    key_events: list[str] = field(default_factory=list)
    questions_raised: list[str] = field(default_factory=list)

    # Scalar ratings (1-5 scale inferred by model)
    energy_level: Optional[int] = None
    social_connectedness: Optional[int] = None
    forward_momentum: Optional[int] = None

    # Entity tracking: {entity_label: "short interaction note"}
    entities: dict[str, str] = field(default_factory=dict)

    # ── Machine-readable index fields ──────────────────────────────────────
    # Primary agent-navigation layer. Dense, structured, grep-friendly.

    # Fast filter fields (new) — allow agent to filter without reading full summaries
    overall_vibe: Optional[str] = None         # THRIVING | POSITIVE | NEUTRAL | MIXED | NEGATIVE | CRISIS
    time_of_day_texture: Optional[str] = None  # MORNING_HEAVY | EVENING_HEAVY | BALANCED | ALL_DAY

    # Existing navigation fields
    peak_moment: Optional[str] = None
    scalar_metrics: Optional[str] = None           # raw block: ENERGY_LEVEL, EMOTIONAL_VALENCE etc.
    narrative_threads: Optional[str] = None        # raw block: thread tracking
    significant_delta: Optional[str] = None        # raw block: delta vs prior period
    physiological_flags: Optional[str] = None      # raw block: sleep, illness, substances
    relational_map: Optional[str] = None           # raw block: full entity valence table
    entity_mentions: Optional[str] = None          # raw block: structured per-person canonical entries
    avoidance_signals: Optional[str] = None        # raw block: conspicuous absences
    growth_markers: Optional[str] = None           # raw block: insight / growth moments
    coping_mechanisms: Optional[str] = None        # raw block: coping strategies
    self_perception_snapshot: Optional[str] = None # raw block: agency, self-criticism
    values_in_tension: Optional[str] = None        # raw block: competing values / internal conflicts
    context_bridge: Optional[str] = None           # raw block: carrying_forward / watch_for_next

    # Processing metadata
    thinking_trace: Optional[str] = None
    source_units: list[str] = field(default_factory=list)   # child period_start strings
    source_entry_count: int = 0
    token_count_input: Optional[int] = None
    token_count_output: Optional[int] = None
    model_used: Optional[str] = None
    mode: Optional[str] = None                 # e.g. "isolated" | "historical_3"
    processing_time_seconds: Optional[float] = None
    timestamp_processed: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "SummaryUnit":
        # Handle legacy fields gracefully (old summaries may have central_themes, retrospective_note)
        known = cls.__dataclass_fields__
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)

    @classmethod
    def from_json(cls, s: str) -> "SummaryUnit":
        return cls.from_dict(json.loads(s))


# ─────────────────────────────────────────────
#  Entity Dossier schema
# ─────────────────────────────────────────────

@dataclass
class EntityEntry:
    """A single period's note about a tracked entity."""
    period_start: str
    period_end: str
    unit: str
    note: str


@dataclass
class EntityDossier:
    """Collected notes for a single tracked entity, across all time units."""
    entity_label: str                        # primary name as defined by user
    aliases: list[str] = field(default_factory=list)   # known aliases
    entries: list[EntityEntry] = field(default_factory=list)
    synthesis: Optional[str] = None         # filled by the synthesis prompt

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ─────────────────────────────────────────────
#  Entity Profile schema (profile system)
# ─────────────────────────────────────────────

@dataclass
class TimestampedKnowledge:
    """A single moment of knowledge about a person, anchored to when it was learned."""
    valid_from: str                 # YYYY-MM-DD: first diary entry that established this
    source_entries: list[str]       # list of YYYY-MM-DD.json filenames
    content: str                    # what was learned / observed
    emotional_valence: str          # warm | neutral | tense | conflicted | absent
    tags: list[str] = field(default_factory=list)
    valid_until: Optional[str] = None  # None = still valid / open-ended


@dataclass
class EntityProfile:
    """Persistent, timestamped knowledge graph entry for one person or entity."""
    id: str                         # snake_case canonical ID, e.g. "anna_korhonen"
    display_name: str
    aliases: list[str] = field(default_factory=list)
    role_in_authors_life: str = ""
    first_mentioned_in_diary: str = ""
    stable_facts: dict = field(default_factory=dict)  # static info: relationship, location, etc.
    timestamped_knowledge: list[TimestampedKnowledge] = field(default_factory=list)
    relationship_arc_summary: Optional[str] = None
    arc_last_updated: Optional[str] = None

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EntityProfile":
        tk_list = [
            TimestampedKnowledge(**e)
            for e in d.pop("timestamped_knowledge", [])
        ]
        profile = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        profile.timestamped_knowledge = tk_list
        return profile

    def as_of(self, date: Optional[str] = None) -> "EntityProfile":
        """
        Return a copy of this profile filtered to only knowledge valid up to `date`.
        If date is None, all knowledge is returned (retrospective mode).
        stable_facts are always included.
        """
        import dataclasses
        copy = dataclasses.replace(self)
        if date is not None:
            copy.timestamped_knowledge = [
                tk for tk in self.timestamped_knowledge
                if tk.valid_from <= date
            ]
        return copy
