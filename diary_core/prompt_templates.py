"""
prompt_templates.py
===================
Prompt templates for the hierarchical diary summarization system.

ARCHITECTURE NOTE:
  Every summary has two distinct sections, optimized for different consumers:

  1. MACHINE-READABLE INDEX  (fields: OVERALL_VIBE,
     TIME_OF_DAY_TEXTURE, KEY_EVENTS, SCALAR_METRICS,
     PEAK_MOMENT, NARRATIVE_THREADS, SIGNIFICANT_DELTA, PHYSIOLOGICAL_FLAGS,
     RELATIONAL_MAP, AVOIDANCE_SIGNALS, GROWTH_MARKERS, COPING_MECHANISMS,
     VALUES_IN_TENSION, SELF_PERCEPTION_SNAPSHOT, CONTEXT_BRIDGE)
     → Used by the agent during Phase 1-2 navigation. Must be dense, specific,
       low-ambiguity, and source-linked. The agent will read ONLY these fields
       during traversal. Prefer exact terms over nuanced prose here.

  2. HUMAN-READABLE NARRATIVE  (fields: SUMMARY, EMOTIONAL_TONE, QUESTIONS_RAISED,
     ENTITIES)
     → Rich prose for human reading and Phase 3 agentic synthesis.

TEMPORAL RULES:
  Day + Week summaries:  forward-only (Mode B). No future context. Must reflect
                         only what the author knew at the time.
  Month + Year summaries: retrospective synthesis. May reference how threads resolved.

Template placeholders:
    {date}     — the period date/label
    {entries}  — full text / child summaries for this period
    {history}  — previous N period summaries (Mode B context)
    {n}        — number of history periods provided
"""


# ─────────────────────────────────────────────────────────────────────────────
#  SHARED FIELD DEFINITIONS
#  These instruction blocks are embedded into every prompt to ensure
#  consistent field output across all levels and modes.
# ─────────────────────────────────────────────────────────────────────────────

_MACHINE_INDEX_INSTRUCTIONS = """\
=== MACHINE-READABLE INDEX (agent navigation — be dense, specific, source-linked) ===

OVERALL_VIBE: (single keyword — the dominant emotional register of this period.
  Choose EXACTLY ONE: THRIVING | POSITIVE | NEUTRAL | MIXED | NEGATIVE | CRISIS
  THRIVING = exceptionally good, energised, flourishing
  POSITIVE = generally good mood, things going well
  NEUTRAL  = flat, uneventful, neither good nor bad
  MIXED    = significant ups AND downs in the same period
  NEGATIVE = predominantly low, sad, stressed, or drained
  CRISIS   = acute distress, breakdown, emergency, or severe instability)

TIME_OF_DAY_TEXTURE: (single keyword — when did most of the significant activity / writing occur?
  MORNING_HEAVY | EVENING_HEAVY | BALANCED | ALL_DAY
  If unclear from entries, write: BALANCED)

KEY_EVENTS:
- [YYYY-MM-DD]: (one concrete, factual sentence of what actually happened, no vague language.
  Name all people involved. Mark significance as: low / medium / high.
  The agent uses source_date to navigate directly to raw entries.)
(Repeat for EVERY significant event. Low-significance events still get logged if they
 were explicitly recorded.)

PEAK_MOMENT: (single sentence — the one most significant moment of this period and its date.
  Format: "YYYY-MM-DD — [what happened]". This is the agent's highest-confidence anchor
  when it needs to read just one raw entry from this period.)

SCALAR_METRICS:
  ENERGY_LEVEL: (1-5, where 1=exhausted/depleted, 5=vibrant/high-energy)
  SOCIAL_CONNECTEDNESS: (1-5, where 1=isolated/withdrawn, 5=deeply connected)
  FORWARD_MOMENTUM: (1-5, where 1=stuck/paralyzed, 5=clear direction/progress)
  EMOTIONAL_VALENCE: (1-5, where 1=very negative/distressed, 5=very positive/content)
  SCALAR_NOTES: (1-2 sentences explaining any notable metric. Flag inflection points.)

NARRATIVE_THREADS:
- thread_[descriptive_slug]: status: new | ongoing | escalating | resolving | resolved | dormant
  description: (one sentence: what is this arc about?)
  first_seen: YYYY-MM-DD (date this thread first appeared in the diary)
  last_active: YYYY-MM-DD (most recent date this thread was active)
(Track ALL active threads, not just new ones. Use "dormant" if a thread was active
 before this period but not mentioned here. This enables the agent to track slow arcs.
 Use consistent thread slugs across summaries so the agent can join them over time.)

SIGNIFICANT_DELTA: (compared to the PREVIOUS period at this same level)
  mood_shift: (one sentence — did emotional tone improve, worsen, or hold steady?)
  energy_shift: (one sentence — how did energy change?)
  new_threads: (comma-separated new thread slugs that appeared for the first time, or: none)
  resolved_threads: (comma-separated thread slugs that closed this period, or: none)
  new_entities: (names/roles of people/places that appear for the first time, or: none)
  notable_change: (one sentence — the most important thing that changed vs. last period)
(If this is the FIRST summary in the dataset, write FIRST_ENTRY for all fields.)

PHYSIOLOGICAL_FLAGS:
  SLEEP: normal | disrupted | poor | good | not_mentioned
  ILLNESS: (describe any illness, injury, or physical symptom; "none" if absent)
  ENERGY_PATTERN: (brief note on how physical energy moved through the period)
  SOMATIC_NOTES: (any body sensations, chronic symptoms, physical complaints mentioned; "none" if absent)
  SUBSTANCES: (any mention of alcohol, caffeine, medication, drugs — factual, no judgment; "none" if absent)

RELATIONAL_MAP:
- [name or role]: valence: warm | neutral | tense | absent | conflicted | missing_them
  interaction_type: (e.g., "in-person conversation", "phone call", "argument", "mentioned only")
  emotional_weight: low | medium | high
  notable: (one sentence on anything significant; "none" if routine)
(Include ALL people mentioned, not just significant ones. Absence of a person previously
 important should also be logged with valence: absent. This gives the agent a complete
 relational picture even when a key person disappears from the entries.)

ENTITY_MENTIONS:
- name_as_written: (name or role exactly as it appears in the entries, e.g. "Anna", "my supervisor")
  normalized_name: (best-guess canonical snake_case ID, e.g. "anna_korhonen", "dr_maria")
  valence: warm | neutral | tense | conflicted | absent
  interaction_summary: (1-2 detailed sentences: what happened between the author and this person?
    How did the author feel? Be specific — name the context, mood, and any outcome.)
  source_date: YYYY-MM-DD
(List ALL people mentioned. If no people appear, write: none)

AVOIDANCE_SIGNALS:
- (Note topics or people conspicuously NOT written about, given prior context.
  Format: "No mention of [X] despite [reason it would be expected]."
  If nothing notable absent, write: none)

GROWTH_MARKERS:
- (Moments of insight, stated realizations, or first-time behavior changes.
  Format: "YYYY-MM-DD — [what insight or growth occurred]"
  If none present, write: none)

COPING_MECHANISMS:
  OBSERVED: (comma-separated list of what the author actually did when stressed or challenged,
    e.g., exercise, journaling, social_withdrawal, reached_out_to_friend,
    alcohol, creative_work, distraction, avoidance. "none" if absent.)
  PATTERN_NOTES: (one sentence on the dominant coping pattern, if any)

VALUES_IN_TENSION:
- (Describe any situation where the author seemed caught between two things they care about.
  Format: "Wants [X] but also [Y] — visible in [brief example from the entries]."
  If none present, write: none)

SELF_PERCEPTION_SNAPSHOT:
  AGENCY: high | medium | low  (how much control did the author feel over their life?)
  SELF_CRITICISM: elevated | moderate | low | not_present
  IDENTITY_NOTES: (one sentence — any questioning of role, self-concept, or purpose; "none" if absent)
  NOTABLE_SELF_TALK: (direct or paraphrased self-referential language from the entries;
    quote sparingly and briefly. "none" if absent.)

CONTEXT_BRIDGE:
  CARRYING_FROM_PREVIOUS: (what unresolved threads, moods, or situations were inherited
    from the prior period? Be specific. "none" if first entry.)
  OPEN_QUESTIONS_ENTERING: (comma-separated list of unresolved questions or tensions at the
    START of this period, or: none)
  SETS_UP_FOR_NEXT: (one sentence — what is left unresolved or building as this period ends?
    What should the next summary watch for?)
"""

_MACRO_INDEX_INSTRUCTIONS = """\
=== MACRO MACHINE-READABLE INDEX (agent navigation — be dense, specific, source-linked) ===

OVERALL_VIBE: (single keyword — the dominant emotional register of this period.
  Choose EXACTLY ONE: THRIVING | POSITIVE | NEUTRAL | MIXED | NEGATIVE | CRISIS
  THRIVING = exceptionally good, energised, flourishing
  POSITIVE = generally good mood, things going well
  NEUTRAL  = flat, uneventful, neither good nor bad
  MIXED    = significant ups AND downs in the same period
  NEGATIVE = predominantly low, sad, stressed, or drained
  CRISIS   = acute distress, breakdown, emergency, or severe instability)

KEY_EVENTS:
- [YYYY-MM-DD]: (one concrete, factual sentence of what actually happened, no vague language.
  Name all people involved. Mark significance as: low / medium / high.
  The agent uses source_date to navigate directly to raw entries.)
(Repeat for EVERY significant event. Low-significance events still get logged if they
 were explicitly recorded.)

PEAK_MOMENT: (single sentence — the one most significant moment of this period and its date.
  Format: "YYYY-MM-DD — [what happened]". This is the agent's highest-confidence anchor
  when it needs to read just one raw entry from this period.)

SCALAR_METRICS:
  ENERGY_LEVEL: (1-5, where 1=exhausted/depleted, 5=vibrant/high-energy)
  SOCIAL_CONNECTEDNESS: (1-5, where 1=isolated/withdrawn, 5=deeply connected)
  FORWARD_MOMENTUM: (1-5, where 1=stuck/paralyzed, 5=clear direction/progress)
  EMOTIONAL_VALENCE: (1-5, where 1=very negative/distressed, 5=very positive/content)
  SCALAR_NOTES: (1-2 sentences explaining any notable metric. Flag inflection points.)

NARRATIVE_THREADS:
- thread_[descriptive_slug]: status: new | ongoing | escalating | resolving | resolved | dormant
  description: (one sentence: what is this arc about?)
  first_seen: YYYY-MM-DD (date this thread first appeared in the diary)
  last_active: YYYY-MM-DD (most recent date this thread was active)
(Track ALL active threads, not just new ones. Use "dormant" if a thread was active
 before this period but not mentioned here. This enables the agent to track slow arcs.
 Use consistent thread slugs across summaries so the agent can join them over time.)

SIGNIFICANT_DELTA: (compared to the PREVIOUS period at this same level)
  mood_shift: (one sentence — did emotional tone improve, worsen, or hold steady?)
  energy_shift: (one sentence — how did energy change?)
  new_threads: (comma-separated new thread slugs that appeared for the first time, or: none)
  resolved_threads: (comma-separated thread slugs that closed this period, or: none)
  new_entities: (names/roles of people/places that appear for the first time, or: none)
  notable_change: (one sentence — the most important thing that changed vs. last period)
(If this is the FIRST summary in the dataset, write FIRST_ENTRY for all fields.)

PHYSIOLOGICAL_FLAGS:
  SLEEP: normal | disrupted | poor | good | not_mentioned
  ILLNESS: (describe any illness, injury, or physical symptom; "none" if absent)
  ENERGY_PATTERN: (brief note on how physical energy moved through the period)
  SOMATIC_NOTES: (any body sensations, chronic symptoms, physical complaints mentioned; "none" if absent)
  SUBSTANCES: (any mention of alcohol, caffeine, medication, drugs — factual, no judgment; "none" if absent)

RELATIONAL_MAP:
- [name or role]: valence: warm | neutral | tense | absent | conflicted | missing_them
  interaction_type: (e.g., "in-person conversation", "phone call", "argument", "mentioned only")
  emotional_weight: low | medium | high
  notable: (one sentence on anything significant; "none" if routine)
(Include ALL people mentioned, not just significant ones. Absence of a person previously
 important should also be logged with valence: absent. This gives the agent a complete
 relational picture even when a key person disappears from the entries.)

ENTITY_MENTIONS:
- name_as_written: (name or role exactly as it appears in the entries, e.g. "Anna", "my supervisor")
  normalized_name: (best-guess canonical snake_case ID, e.g. "anna_korhonen", "dr_maria")
  valence: warm | neutral | tense | conflicted | absent
  interaction_summary: (1-2 detailed sentences: what happened between the author and this person?
    How did the author feel? Be specific — name the context, mood, and any outcome.)
  source_date: YYYY-MM-DD
(List ALL people mentioned. If no people appear, write: none)

AVOIDANCE_SIGNALS:
- (Note topics or people conspicuously NOT written about, given prior context.
  Format: "No mention of [X] despite [reason it would be expected]."
  If nothing notable absent, write: none)

GROWTH_MARKERS:
- (Moments of insight, stated realizations, or first-time behavior changes.
  Format: "YYYY-MM-DD — [what insight or growth occurred]"
  If none present, write: none)

COPING_MECHANISMS:
  OBSERVED: (comma-separated list of what the author actually did when stressed or challenged,
    e.g., exercise, journaling, social_withdrawal, reached_out_to_friend,
    alcohol, creative_work, distraction, avoidance. "none" if absent.)
  PATTERN_NOTES: (one sentence on the dominant coping pattern, if any)

VALUES_IN_TENSION:
- (Describe any situation where the author seemed caught between two things they care about.
  Format: "Wants [X] but also [Y] — visible in [brief example from the entries]."
  If none present, write: none)

SELF_PERCEPTION_SNAPSHOT:
  AGENCY: high | medium | low  (how much control did the author feel over their life?)
  SELF_CRITICISM: elevated | moderate | low | not_present
  IDENTITY_NOTES: (one sentence — any questioning of role, self-concept, or purpose; "none" if absent)
  NOTABLE_SELF_TALK: (direct or paraphrased self-referential language from the entries;
    quote sparingly and briefly. "none" if absent.)

CONTEXT_BRIDGE:
  CARRYING_FROM_PREVIOUS: (what unresolved threads, moods, or situations were inherited
    from the prior period? Be specific. "none" if first entry.)
  OPEN_QUESTIONS_ENTERING: (comma-separated list of unresolved questions or tensions at the
    START of this period, or: none)
  SETS_UP_FOR_NEXT: (one sentence — what is left unresolved or building as this period ends?
    What should the next summary watch for?)
"""


_HUMAN_NARRATIVE_INSTRUCTIONS = """\
=== HUMAN-READABLE NARRATIVE (rich prose — for human reading and broad agentic synthesis) ===

SUMMARY: (Multi-paragraph, highly concrete narrative. Use specific names and factual events —
  never "a friend" when you know the name, never "a bad day" when you can say what happened.
  Capture the emotional arc and narrative shape of this period. A reader who hasn't seen the
  raw entries should finish this section with a genuine sense of what it was like to live
  through this period. Aim for depth over breadth.)

EMOTIONAL_TONE: (A descriptive phrase capturing the emotional texture and trajectory of this
  period. Not just a single word — describe the movement: e.g., "Started the week in quiet
  anxiety, broke open midweek into something rawer, ended with a fragile sense of resolution.")

QUESTIONS_RAISED:
- (Unresolved threads, lingering tensions, or open questions worth tracking in future entries.
  These should feel like things a good therapist would circle back to. Be specific.)
"""


# ─────────────────────────────────────────────────────────────────────────────
#  ROLE-AWARE ADDENDUM
#  Inserted into the system prompt only when assistant entries are included.
#  Entries are pre-labeled as [HH:MM - user] or [HH:MM - assistant] in the text.
# ─────────────────────────────────────────────────────────────────────────────

ROLE_AWARE_SYSTEM_ADDENDUM = """\

IMPORTANT — MIXED ENTRY TYPES:
The entries below include two types of content, clearly labeled by role:
  [HH:MM - user]: written by the user themselves — diary-style reflections,
    events, emotions, thoughts. This is your PRIMARY source material.
  [HH:MM - assistant]: AI-generated responses from a journaling/therapy assistant.
    These are NOT the user's own words. Treat them as secondary context only.

How to use each type:
  - USER entries: summarize and analyze these faithfully. They are the ground truth
    of what the user experienced, felt, and thought.
  - ASSISTANT entries: use these to understand what themes were explored or what
    the user seemed to be working through. You may use assistant observations to
    add nuance or read between the lines — but never present assistant-written
    content as the user's own thoughts or feelings. If you draw on an assistant
    entry, frame it carefully, e.g. "the user appeared to be grappling with..."
    rather than stating it as a direct fact.
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Mode A — Isolated (no context)
# ─────────────────────────────────────────────────────────────────────────────

DAY_ISOLATED = f"""\
You are an insightful, exhaustive personal historian analyzing the private diary entries of the
Diarist (the author). Extract highly detailed concrete facts, specific names, complete thoughts,
and deep emotional truths. Never rely on vague pronouns or generalizations.

Your output serves TWO purposes:
  1. A machine-readable index for an AI search agent to navigate this data efficiently.
  2. A human-readable narrative for direct reading and broad thematic synthesis.

IMPORTANT: In the machine-readable index fields, prioritize specificity and searchability
over literary quality. Save nuance and rich prose for the NARRATIVE section only.

DIARY ENTRIES FOR {{date}}:
{{entries}}

Output EXACTLY the following sections in order. Do not add, remove, or rename any section.

{_MACHINE_INDEX_INSTRUCTIONS}

{_HUMAN_NARRATIVE_INSTRUCTIONS}

Sentence counts: SUMMARY = 6-10 sentences. All other fields per instructions above.
"""

WEEK_ISOLATED = f"""\
You are an insightful, exhaustive personal historian analyzing the private diary of the Diarist.
You are synthesizing day-level summaries into a week-level summary.

Your output serves TWO purposes:
  1. A machine-readable index for an AI search agent to navigate this data efficiently.
  2. A human-readable narrative for direct reading and broad thematic synthesis.

NOTE: Week summaries are FORWARD-ONLY. Reflect only what was known during the week.
Do not interpret events with hindsight. Capture the week as it felt while it unfolded.

DAY-LEVEL SUMMARIES FOR THE WEEK OF {{date}}:
{{entries}}

GRANDCHILD (RAW) ENTRIES:
(To provide maximum detail and concrete grounding, here are the raw daily entries for this week.
Use these to enrich your summary with specific quotes, exact events, and deep emotional nuance.
Do not get confused by the overlap between the summaries above and these grandchild entries—they describe
the exact same underlying period. Treat them as the primary high-resolution source.)
{{grandchild_items}}

Output EXACTLY the following sections in order. Do not add, remove, or rename any section.
For KEY_EVENTS: aggregate the most significant events from each day. Include the source
day date in brackets for every event.

{_MACHINE_INDEX_INSTRUCTIONS}

{_HUMAN_NARRATIVE_INSTRUCTIONS}

Sentence counts: SUMMARY = 8-12 sentences. All other fields per instructions above.
"""

MONTH_ISOLATED = f"""\
You are an insightful, exhaustive personal historian analyzing the private diary of the Diarist.
You are synthesizing week-level summaries into a retrospective month-level summary.

Your output serves TWO purposes:
  1. A machine-readable index for an AI search agent to navigate this data efficiently.
  2. A human-readable narrative for direct reading and broad thematic synthesis.

NOTE: Month summaries are RETROSPECTIVE. You have full visibility into the entire month.
You may reference how threads resolved, note patterns that only became clear in hindsight,
and synthesize across the full arc. Label retrospective observations as such.

WEEK-LEVEL SUMMARIES FOR {{date}}:
{{entries}}

GRANDCHILD DAY SUMMARIES:
(To provide maximum detail and concrete grounding, here are the underlying daily summaries for this month.
Use these to enrich your summary with specific events and emotional nuance instead of relying only on the weekly rollups.)
{{grandchild_items}}

Output EXACTLY the following sections in order. Do not add, remove, or rename any section.
For KEY_EVENTS: include the most significant events of the month with the source week date.

{_MACRO_INDEX_INSTRUCTIONS}

{_HUMAN_NARRATIVE_INSTRUCTIONS}

Sentence counts: SUMMARY = 10-15 sentences. All other fields per instructions above.
"""

YEAR_ISOLATED = f"""\
You are an insightful, exhaustive personal historian analyzing the private diary of the Diarist.
You are synthesizing month-level summaries into a retrospective year-level summary.

Your output serves TWO purposes:
  1. A machine-readable index for an AI search agent to navigate this data efficiently.
  2. A human-readable narrative for direct reading and broad thematic synthesis.

NOTE: Year summaries are fully RETROSPECTIVE. Synthesize with complete hindsight.
Identify the largest transformations, the deepest recurring patterns, and the questions
that defined this year. Trace narrative arcs from their earliest signs to their resolution
(or continued openness).

MONTH-LEVEL SUMMARIES FOR THE YEAR {{date}}:
{{entries}}

GRANDCHILD WEEK SUMMARIES:
(For deeper detail, here are the underlying week summaries for this year.)
{{grandchild_items}}

Output EXACTLY the following sections in order. Do not add, remove, or rename any section.
For KEY_EVENTS: include only the year's most significant events with the source month.

{_MACRO_INDEX_INSTRUCTIONS}

{_HUMAN_NARRATIVE_INSTRUCTIONS}

Sentence counts: SUMMARY = 15-20 sentences. This is the highest-level reflection.
Be extremely thorough. Identify what this year was fundamentally about.
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Mode B — Historical context (forward-only)
# ─────────────────────────────────────────────────────────────────────────────

DAY_HISTORICAL = f"""\
You are an insightful, exhaustive personal historian analyzing the private diary of the Diarist.

RECENT CONTEXT (previous {{n}} days — raw diary entries for background. Do not re-summarize these):
{{history}}

TODAY'S DIARY ENTRIES ({{date}}):
{{entries}}

Using the recent context to track continuity and change, write a comprehensive structured
analysis of TODAY ONLY. The historical context helps you identify what's new, what's
continuing, and what has shifted — but your output should document today, not the history.

Your output serves TWO purposes:
  1. A machine-readable index for an AI search agent (dense, specific, source-linked).
  2. A human-readable narrative (rich, concrete, emotionally honest).

NOTE: This is a forward-only summary. Do not use hindsight. Reflect only what was
known and felt on {{date}}.

Output EXACTLY the following sections in order. Do not add, remove, or rename any section.

{_MACHINE_INDEX_INSTRUCTIONS}

{_HUMAN_NARRATIVE_INSTRUCTIONS}

Sentence counts: SUMMARY = 6-10 sentences. All other fields per instructions above.
For SIGNIFICANT_DELTA: compare explicitly to the most recent day in the history context.
For NARRATIVE_THREADS: carry forward any threads visible in history; mark new ones.
"""

WEEK_HISTORICAL = f"""\
You are an insightful, exhaustive personal historian analyzing the private diary of the Diarist.

RECENT CONTEXT (previous {{n}} weeks — for background only, do not re-summarize):
{{history}}

THIS WEEK'S DAY-LEVEL SUMMARIES (week of {{date}}):
{{entries}}

THIS WEEK'S GRANDCHILD (RAW) ENTRIES:
(To provide maximum detail and concrete grounding, here are the raw daily entries for this week.
Use these to enrich your summary with specific quotes, exact events, and deep emotional nuance.
Do not get confused by the overlap between the summaries above and these grandchild entries—they describe
the exact same underlying period. Treat them as the primary high-resolution source.)
{{grandchild_items}}

Synthesize into a week-level summary. Use history to track thread continuity, emotional
trajectory, and what has changed — but your output should document THIS WEEK only.

Your output serves TWO purposes:
  1. A machine-readable index for an AI search agent (dense, specific, source-linked).
  2. A human-readable narrative (rich, concrete, emotionally honest).

NOTE: Forward-only. Do not use hindsight. Reflect only what was known during this week.

Output EXACTLY the following sections in order. Do not add, remove, or rename any section.

{_MACHINE_INDEX_INSTRUCTIONS}

{_HUMAN_NARRATIVE_INSTRUCTIONS}

Sentence counts: SUMMARY = 8-12 sentences. All other fields per instructions above.
For SIGNIFICANT_DELTA: compare explicitly to the previous week in history.
For NARRATIVE_THREADS: carry forward active threads from history with updated status.
"""

MONTH_HISTORICAL = f"""\
You are an insightful, exhaustive personal historian analyzing the private diary of the Diarist.

RECENT CONTEXT (previous {{n}} months — for background only, do not re-summarize):
{{history}}

THIS MONTH'S WEEK-LEVEL SUMMARIES ({{date}}):
{{entries}}

THIS MONTH'S GRANDCHILD DAY SUMMARIES:
(To provide maximum detail and concrete grounding, here are the underlying daily summaries for this month.
Use these to enrich your summary with specific events and emotional nuance instead of relying only on the weekly rollups.)
{{grandchild_items}}

Synthesize into a retrospective month-level summary. Use prior months to track long-running
arcs, emotional drift, and what has fundamentally changed vs. what has persisted.

Your output serves TWO purposes:
  1. A machine-readable index for an AI search agent (dense, specific, source-linked).
  2. A human-readable narrative (rich, concrete, emotionally honest).

NOTE: Month summaries are RETROSPECTIVE. You have full visibility into the whole month
and prior months. Note patterns that only became clear in hindsight. Label these clearly.

Output EXACTLY the following sections in order. Do not add, remove, or rename any section.

{_MACRO_INDEX_INSTRUCTIONS}

{_HUMAN_NARRATIVE_INSTRUCTIONS}

Sentence counts: SUMMARY = 10-15 sentences. All other fields per instructions above.
For SIGNIFICANT_DELTA: compare explicitly to the previous month in history.
For NARRATIVE_THREADS: trace thread evolution across the full span visible in history.
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Template registry
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATES = {
    "day_isolated":        DAY_ISOLATED,
    "week_isolated":       WEEK_ISOLATED,
    "month_isolated":      MONTH_ISOLATED,
    "year_isolated":       YEAR_ISOLATED,
    "day_historical":      DAY_HISTORICAL,
    "week_historical":     WEEK_HISTORICAL,
    "month_historical":    MONTH_HISTORICAL,
}


def get_template(key: str) -> str:
    """Return a template by key. Raises KeyError for unknown keys."""
    if key not in TEMPLATES:
        raise KeyError(
            f"Unknown template key: {key!r}. Available: {list(TEMPLATES)}"
        )
    return TEMPLATES[key]