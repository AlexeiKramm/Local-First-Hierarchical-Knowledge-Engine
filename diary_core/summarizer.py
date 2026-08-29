"""
summarizer.py
=============
Core summarization engine.

Supports:
  Mode A — Isolated:         no context from other periods
  Mode B — Historical:       prepends raw entries from previous N days as context

The summarizer is unit-agnostic (works at day, week, month, year level).
A pause_event (threading.Event) can be used to pause/resume from the GUI.

Concurrency:
  summarize_days()          — synchronous, sequential (the safe default)
  summarize_days_parallel() — async, uses asyncio.Semaphore to cap in-flight requests.
                              All day-level prompts are independent (context = raw text,
                              not previously generated summaries), so full parallelism
                              is safe at the day level. Week/month/year remain sequential.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from typing import Callable, Optional

from .input_loader import DayEntries
from .llm_client import LLMClient
from .progress_manager import ProgressManager
from .prompt_templates import get_template
from .schema import SummaryUnit
from .token_estimator import estimate as estimate_tokens


# ─────────────────────────────────────────────
#  Summarization config
# ─────────────────────────────────────────────

class SummarizationConfig:
    def __init__(
        self,
        mode: str = "isolated",           # "isolated" | "historical"
        history_n: int = 3,               # Mode B: number of previous raw days as context
        context_window: int = 262_144,
        output_budget: int = 1024,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        output_format: str = "section",   # "section" | "json"
        custom_templates: Optional[dict] = None,
        concurrency: int = 1,             # 1 = sequential; >1 = parallel (day level only)
        api_key: str = "",                # OpenRouter / cloud API key
        inject_grandchild_data: bool = False,  # If True, data from 2 levels lower in hierarchy is injected into prompts
    ):
        self.mode = mode
        self.history_n = history_n
        self.context_window = context_window
        self.output_budget = output_budget
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.output_format = output_format
        self.custom_templates = custom_templates or {}
        self.concurrency = concurrency
        self.api_key = api_key
        self.inject_grandchild_data = inject_grandchild_data

    def template(self, key: str) -> str:
        return self.custom_templates.get(key, get_template(key))


# ─────────────────────────────────────────────
#  Text assembly helpers
# ─────────────────────────────────────────────

def _format_summary_as_context(s: SummaryUnit) -> str:
    """Format a SummaryUnit into a concise context block for higher-level rollups."""
    lines = [f"[{s.period_start} — {s.period_end}]"]
    if s.summary:
        lines.append(f"Summary: {s.summary}")
    if s.emotional_tone:
        lines.append(f"Tone: {s.emotional_tone}")
    if s.key_events:
        lines.append(f"Events: {'; '.join(s.key_events)}")
    if s.overall_vibe:
        lines.append(f"Vibe: {s.overall_vibe}")
    if s.peak_moment:
        lines.append(f"Peak: {s.peak_moment}")
    if s.narrative_threads:
        lines.append(f"Threads: {s.narrative_threads.replace(chr(10), ' ')}")
    if s.scalar_metrics:
        lines.append(f"Metrics: {s.scalar_metrics.replace(chr(10), ' ')}")
    if s.significant_delta:
        lines.append(f"Delta: {s.significant_delta.replace(chr(10), ' ')}")
    if s.physiological_flags:
        lines.append(f"Physio: {s.physiological_flags.replace(chr(10), ' ')}")
    if s.relational_map:
        lines.append(f"Relations: {s.relational_map.replace(chr(10), ' ')}")
    if s.avoidance_signals:
        lines.append(f"Avoidance: {s.avoidance_signals.replace(chr(10), ' ')}")
    if s.growth_markers:
        lines.append(f"Growth: {s.growth_markers.replace(chr(10), ' ')}")
    if s.coping_mechanisms:
        lines.append(f"Coping: {s.coping_mechanisms.replace(chr(10), ' ')}")
    if s.self_perception_snapshot:
        lines.append(f"Self-perception: {s.self_perception_snapshot.replace(chr(10), ' ')}")
    if s.values_in_tension:
        lines.append(f"Values-tension: {s.values_in_tension.replace(chr(10), ' ')}")
    if s.context_bridge:
        lines.append(f"Bridge: {s.context_bridge.replace(chr(10), ' ')}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  Summarizer class
# ─────────────────────────────────────────────

LogCallback = Callable[[str], None]


class Summarizer:
    """
    Orchestrates the summarization of a list of text units (days or weeks).

    All modes operate on a pre-loaded list of 'source_units' — either DayEntries
    (for the base unit) or SummaryUnit lists (for higher levels).
    """

    def __init__(
        self,
        client: LLMClient,
        progress: ProgressManager,
        config: SummarizationConfig,
        log: Optional[LogCallback] = None,
        pause_event: Optional[threading.Event] = None,
        stop_event: Optional[threading.Event] = None,
    ):
        self.client = client
        self.progress = progress
        self.config = config
        self.log = log or (lambda msg: None)
        self.pause_event = pause_event
        self.stop_event = stop_event

    def _wait_if_paused(self):
        """Block until pause_event is cleared. Checks stop_event too."""
        if self.pause_event:
            while self.pause_event.is_set():
                if self.stop_event and self.stop_event.is_set():
                    return
                import time
                time.sleep(0.2)

    def _is_stopped(self) -> bool:
        return bool(self.stop_event and self.stop_event.is_set())

    # ── Day-level summarization (sequential) ──────────────────────────────

    def summarize_days(
        self,
        days: list[DayEntries],
        target_range: Optional[tuple[str, str]] = None,
    ) -> tuple[list[SummaryUnit], set[str]]:
        """
        Summarize a list of DayEntries sequentially (one at a time).
        target_range: If set, only saves/appends units within this date range.
        Context days before the range are generated silently as backfill if needed.
        """
        results: list[SummaryUnit] = []
        new_day_keys: set[str] = set()
        total = len(days)

        target_total = total
        if target_range:
            target_total = sum(1 for d in days if target_range[0] <= d.date <= target_range[1])
        processed_target_count = 0

        start_idx = 0
        end_date = None
        if target_range:
            start, end_date = target_range
            for i, d in enumerate(days):
                if d.date >= start:
                    start_idx = i
                    break
            # Backfill: need history_n days before range start for context
            start_idx = max(0, start_idx - self.config.history_n)

        for idx, day in enumerate(days):
            self._wait_if_paused()
            if self._is_stopped():
                self.log("⏹ Stop requested.")
                break

            key = day.date
            if target_range:
                if idx < start_idx:
                    continue
                if end_date and key > end_date:
                    break

            in_range = True
            if target_range:
                start, end = target_range
                in_range = (start <= key <= end)

            if self.progress.is_done("day", key):
                self.log(f"  ↷ Skipping {key} (already done)")
                existing = self.progress.load_unit("day", key)
                if existing:
                    results.append(existing)
                if in_range:
                    processed_target_count += 1
                continue

            if target_range and not in_range and key < target_range[0]:
                self.log(f"  [Backfill] Generating missing context for {key}…")
            elif in_range:
                processed_target_count += 1
                self.log(f"  [{processed_target_count}/{target_total}] Summarizing day {key}…")

            summary, response = self._summarize_single_day(days_list=days, idx=idx)
            path = self.progress.save_unit(summary)
            if in_range:
                tok_in = response.input_tokens if response.input_tokens is not None else "?"
                tok_out = response.output_tokens if response.output_tokens is not None else "?"
                self.log(f"  [{processed_target_count}/{target_total}] ✓ {path.name} (Tokens in: {tok_in}, Tokens out: {tok_out} tokens, Duration {response.elapsed_seconds:.1f}s)")
            results.append(summary)
            new_day_keys.add(key)

        return results, new_day_keys

    # ── Day-level summarization (parallel / async) ─────────────────────────

    def summarize_days_parallel(
        self,
        days: list[DayEntries],
        target_range: Optional[tuple[str, str]] = None,
    ) -> tuple[list[SummaryUnit], set[str]]:
        """
        Run day summarization with concurrent async requests.
        All day prompts are independent (raw context only) — safe to fire simultaneously.
        Wraps the async implementation in asyncio.run() for the GUI thread.
        Returns the same (results_list, new_day_keys) tuple as summarize_days().
        """
        return asyncio.run(self._async_summarize_days(days, target_range))

    async def _async_summarize_days(
        self,
        days: list[DayEntries],
        target_range: Optional[tuple[str, str]] = None,
    ) -> tuple[list[SummaryUnit], set[str]]:
        from .llm_client_async import AsyncLLMClient

        async_client = AsyncLLMClient(
            api_base=self.client.api_base,
            model_name=self.client.model_name,
            timeout=self.client.timeout,
            api_key=self.client.api_key,
            app_title=self.client.app_title,
        )

        concurrency = max(1, self.config.concurrency)
        semaphore = asyncio.Semaphore(concurrency)

        target_total = len(days)
        if target_range:
            target_total = sum(1 for d in days if target_range[0] <= d.date <= target_range[1])

        # Determine which indices to process
        start_idx = 0
        end_date = None
        if target_range:
            start, end_date = target_range
            for i, d in enumerate(days):
                if d.date >= start:
                    start_idx = i
                    break
            start_idx = max(0, start_idx - self.config.history_n)

        pending_indices = []
        for idx, day in enumerate(days):
            if target_range:
                if idx < start_idx:
                    continue
                if end_date and day.date > end_date:
                    break
            pending_indices.append(idx)

        results_map: dict[int, SummaryUnit] = {}
        new_day_keys: set[str] = set()
        processed_count = 0
        count_lock = asyncio.Lock()  # guards processed_count increments across concurrent coroutines

        async def process_one(idx: int):
            nonlocal processed_count
            day = days[idx]
            key = day.date

            in_range = True
            if target_range:
                start, end = target_range
                in_range = (start <= key <= end)

            if self.progress.is_done("day", key):
                existing = self.progress.load_unit("day", key)
                if existing:
                    results_map[idx] = existing
                if in_range:
                    async with count_lock:
                        processed_count += 1
                        _count = processed_count
                    self.log(f"  ↷ Skipping {key} (already done) [{_count}/{target_total}]")
                return

            # Bug 4 fix: check stop BEFORE acquiring the semaphore so we don't
            # consume a concurrency slot just to immediately bail out.
            if self._is_stopped():
                return

            async with semaphore:
                system_prompt, user_prompt, n_historic_days = self._build_day_prompt(days_list=days, idx=idx)
                
                # Context safety: reserve output_budget + 5k for overhead
                reserved = self.config.output_budget + 5000
                safety_limit = self.config.context_window - reserved
                est = estimate_tokens(user_prompt, self.config.context_window, reserved)
                
                self.log(f"→ Sending day {key}. ~{est.estimated_tokens} / {safety_limit} tokens. Context: {n_historic_days} historic raw summaries | {concurrency} parallel slots")

                response = await async_client.complete_async(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                    output_format=self.config.output_format,
                    estimated_input_tokens=est.estimated_tokens,
                )

            summary = self._build_summary_unit_from_response(response, day, est)
            # Save immediately after each completion (atomic — protects against crash mid-batch)
            path = self.progress.save_unit(summary)
            results_map[idx] = summary
            new_day_keys.add(key)

            if in_range:
                async with count_lock:
                    processed_count += 1
                    _count = processed_count
                tok_in = response.input_tokens if response.input_tokens is not None else "?"
                tok_out = response.output_tokens if response.output_tokens is not None else "?"
                self.log(f"  [{_count}/{target_total}] ✓ {path.name} (Tokens in: {tok_in}, Tokens out: {tok_out} tokens, Duration {response.elapsed_seconds:.1f}s)")

        tasks = [process_one(idx) for idx in pending_indices]
        await asyncio.gather(*tasks)

        # Return in original order, along with set of newly generated keys
        results_list = [results_map[i] for i in pending_indices if i in results_map]
        return results_list, new_day_keys

    # ── Single-day prompt builder ──────────────────────────────────────────

    def _build_day_prompt(
        self,
        days_list: list[DayEntries],
        idx: int,
    ) -> tuple[str, str, int]:
        """Build (system_prompt, user_prompt, n_historic_days) for one day. Pure function — no LLM calls."""
        mode = self.config.mode
        cfg = self.config
        day = days_list[idx]
        entries_text = day.combined_text

        history_text = "(none)"

        if cfg.history_n > 0:
            from datetime import datetime, timedelta
            target_dt = datetime.strptime(day.date, "%Y-%m-%d")
            min_dt = target_dt - timedelta(days=cfg.history_n)
            min_date_str = min_dt.strftime("%Y-%m-%d")

            hist_items = []
            start_scan = max(0, idx - 365)
            for i in range(start_scan, idx):
                prev_day = days_list[i]
                if prev_day.date >= min_date_str:
                    hist_items.append(f"[{prev_day.date}]\n{prev_day.combined_text}")

            # Dynamic truncation loop — drop oldest if over token budget
            reserved = cfg.output_budget + 2000
            safety_limit = cfg.context_window - reserved
            while True:
                history_text = "\n\n".join(hist_items) if hist_items else "(none)"
                if mode == "isolated":
                    template_key = "day_isolated"
                    user_prompt = cfg.template(template_key).format(
                        date=day.date, entries=entries_text
                    )
                else:
                    template_key = "day_historical"
                    user_prompt = cfg.template(template_key).format(
                        date=day.date, entries=entries_text,
                        history=history_text, n=cfg.history_n
                    )
                system_prompt = "You are a thoughtful personal historian. Follow the exact output format requested."
                est = estimate_tokens(system_prompt + "\n" + user_prompt, cfg.context_window, reserved)
                if est.estimated_tokens > safety_limit and len(hist_items) > 0:
                    hist_items.pop(0)
                else:
                    break
        else:
            if mode == "isolated":
                template_key = "day_isolated"
                user_prompt = cfg.template(template_key).format(
                    date=day.date, entries=entries_text
                )
            else:
                template_key = "day_historical"
                user_prompt = cfg.template(template_key).format(
                    date=day.date, entries=entries_text,
                    history=history_text, n=cfg.history_n
                )
            system_prompt = "You are a thoughtful personal historian. Follow the exact output format requested."

        n_historic_days = len(hist_items) if cfg.history_n > 0 else 0
        return system_prompt, user_prompt, n_historic_days

    def _summarize_single_day(
        self,
        days_list: list[DayEntries],
        idx: int,
    ) -> SummaryUnit:
        """Build prompt for one day, call the LLM synchronously, return a SummaryUnit."""
        day = days_list[idx]
        system_prompt, user_prompt, n_historic_days = self._build_day_prompt(days_list=days_list, idx=idx)

        cfg = self.config
        reserved = cfg.output_budget + 5000
        safety_limit = cfg.context_window - reserved
        est = estimate_tokens(user_prompt, cfg.context_window, reserved)
        self.log(f"→ Sending day {day.date}. ~{est.estimated_tokens} / {safety_limit} tokens. Context: {n_historic_days} historic raw summaries")

        response = self.client.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
            output_format=cfg.output_format,
            estimated_input_tokens=est.estimated_tokens,
        )

        return self._build_summary_unit_from_response(response, day, est), response

    def _build_summary_unit_from_response(self, response, day: DayEntries, est) -> SummaryUnit:
        """Construct a SummaryUnit from an LLMResponse and DayEntries."""
        s = response.structured
        mode = self.config.mode
        cfg = self.config
        return SummaryUnit(
            unit="day",
            period_start=day.date,
            period_end=day.date,
            summary=s.get("summary"),
            emotional_tone=s.get("emotional_tone"),
            energy_level=s.get("energy_level"),
            social_connectedness=s.get("social_connectedness"),
            forward_momentum=s.get("forward_momentum"),
            key_events=s.get("key_events", []),
            questions_raised=s.get("questions_raised", []),
            entities=s.get("entities", {}),
            # New fast-filter fields
            overall_vibe=s.get("overall_vibe"),
            time_of_day_texture=s.get("time_of_day_texture"),
            # Machine-readable index
            peak_moment=s.get("peak_moment"),
            scalar_metrics=s.get("scalar_metrics"),
            narrative_threads=s.get("narrative_threads"),
            significant_delta=s.get("significant_delta"),
            physiological_flags=s.get("physiological_flags"),
            relational_map=s.get("relational_map"),
            entity_mentions=s.get("entity_mentions"),
            avoidance_signals=s.get("avoidance_signals"),
            growth_markers=s.get("growth_markers"),
            coping_mechanisms=s.get("coping_mechanisms"),
            self_perception_snapshot=s.get("self_perception_snapshot"),
            values_in_tension=s.get("values_in_tension"),
            context_bridge=s.get("context_bridge"),
            # Metadata
            thinking_trace=response.parsed.thinking_trace,
            source_units=[day.date],
            source_entry_count=len(day.entries),
            token_count_input=response.input_tokens or est.estimated_tokens,
            token_count_output=response.output_tokens,
            model_used=self.client.model_name,
            mode=f"{mode}_{cfg.history_n}" if mode == "historical" else "isolated",
            processing_time_seconds=round(response.elapsed_seconds, 2),
            timestamp_processed=datetime.now().isoformat(timespec="seconds"),
        )

    # ── Higher-level rollup summarization ─────────────────────────────────

    def summarize_higher_level(
        self,
        unit: str,                        # "week" | "month" | "year"
        groups: list[list[SummaryUnit]],
        prior_summaries: Optional[list[SummaryUnit]] = None,
        grandchild_dict: Optional[dict] = None,
        inject_grandchild_data: Optional[bool] = None,
    ) -> list[SummaryUnit]:
        """
        Summarize groups of lower-level summaries into a higher-level summary.
        E.g., list of week groups → month summaries.
        Always sequential (these levels are few in number and depend on lower-level outputs).
        """
        results: list[SummaryUnit] = []
        prior_summaries = prior_summaries or []
        total = len(groups)
        template_key = f"{unit}_isolated"
        if self.config.mode == "historical":
            historical_key = f"{unit}_historical"
            try:
                self.config.template(historical_key)
                template_key = historical_key
            except KeyError:
                pass

        for idx, group in enumerate(groups):
            if not group:
                continue

            # For weeks, the key is the exact start date (Monday).
            # For months, the key must be YYYY-MM. 
            # For years, it must be YYYY.
            # We use group[-1] to harvest the date string because the first week in a
            # month might mathematically start in the previous month (e.g. 2025-12-29
            # for Jan 2026). Using the last week guarantees we get the target period.
            if unit == "month":
                period_start = group[-1].period_start[:7]
            elif unit == "year":
                period_start = group[-1].period_start[:4]
            else:
                period_start = group[0].period_start
                
            period_end = group[-1].period_end

            self._wait_if_paused()
            if self._is_stopped():
                self.log("⏹ Stop requested.")
                break

            key = period_start
            if self.progress.is_done(unit, key):
                self.log(f"  ↷ Skipping {unit} {key} (already done)")
                existing = self.progress.load_unit(unit, key)
                if existing:
                    results.append(existing)
                continue

            self.log(f"  [{idx + 1}/{total}] Summarizing {unit} {period_start}…")

            entries_text = "\n\n---\n\n".join(
                _format_summary_as_context(s) for s in group
            )

            # Grandchild data injection (data from 2 levels lower in hierarchy)
            inject = inject_grandchild_data if inject_grandchild_data is not None else self.config.inject_grandchild_data
            raw_items = []
            if inject and grandchild_dict:
                for s in group:
                    for child_date in getattr(s, "source_units", []):
                        item = grandchild_dict.get(child_date)
                        if item:
                            text = getattr(item, "combined_text", None) or _format_summary_as_context(item)
                            if text:
                                raw_items.append(f"[{child_date}]\n{text}")

            if self.config.mode == "historical":
                history_list = (prior_summaries + results)[-self.config.history_n:]
                if history_list:
                    history_text = "\n\n---\n\n".join(_format_summary_as_context(s) for s in history_list)
                else:
                    child_unit = "day" if unit == "week" else "week" if unit == "month" else "month"
                    child_sum = self.progress.load_all_units(child_unit)
                    if child_sum:
                        child_sum.sort(key=lambda x: x.period_start)
                        prior_children = [s for s in child_sum if s.period_start < period_start]
                        multiplier = 7 if unit == "week" else 4 if unit == "month" else 12
                        needed_children = self.config.history_n * multiplier
                        fallback_list = prior_children[-needed_children:]
                        if fallback_list:
                            history_text = f"([FALLBACK CONTEXT] The following are '{child_unit}' entries from before this period)\n\n"
                            history_text += "\n\n---\n\n".join(_format_summary_as_context(s) for s in fallback_list)
                        else:
                            history_text = ""
                    else:
                        history_text = ""
            else:
                history_text = ""

            try:
                template = self.config.template(template_key)
            except KeyError:
                template = self.config.template(f"{unit}_isolated")

            if not history_text:
                history_text = "(No entries logged prior to this)"

            system_prompt = "You are a thoughtful personal historian. Follow the exact output format requested."
            reserved = self.config.output_budget + 5000
            safety_limit = self.config.context_window - reserved
            
            # Token-safe builder loop — drop oldest grandchild items if over budget
            grandchild_unit_label = "day" if unit == "week" else "week" if unit == "month" else "month"
            while True:
                grandchild_text = "\n\n---\n\n".join(raw_items) if raw_items else "(none)"
                if "{grandchild_items}" in template:
                    if "{history}" in template:
                        user_prompt = template.format(
                            date=period_start, entries=entries_text,
                            grandchild_items=grandchild_text,
                            history=history_text, n=self.config.history_n
                        )
                    else:
                        user_prompt = template.format(date=period_start, entries=entries_text, grandchild_items=grandchild_text)
                else:
                    if "{history}" in template:
                        user_prompt = template.format(
                            date=period_start, entries=entries_text,
                            history=history_text, n=self.config.history_n
                        )
                    else:
                        user_prompt = template.format(date=period_start, entries=entries_text)

                est = estimate_tokens(system_prompt + "\n" + user_prompt, self.config.context_window, reserved)
                if est.estimated_tokens > safety_limit and len(raw_items) > 0:
                    self.log(f"    ⚠ Context ({est.estimated_tokens} tokens) exceeds safety limit. Dropping oldest grandchild item.")
                    raw_items.pop(0)
                else:
                    break

            est = estimate_tokens(user_prompt, self.config.context_window, reserved)
            child_count = len(group)
            grandchild_count = len(raw_items)
            self.log(f"→ Sending {unit} {period_start}. ~{est.estimated_tokens} / {safety_limit} tokens. Context: {child_count} child summaries + {grandchild_count} grandchild summaries")

            response = self.client.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                output_format=self.config.output_format,
                estimated_input_tokens=est.estimated_tokens,
            )

            s = response.structured
            summary_unit = SummaryUnit(
                unit=unit,
                period_start=period_start,
                period_end=period_end,
                summary=s.get("summary"),
                emotional_tone=s.get("emotional_tone"),
                energy_level=s.get("energy_level"),
                social_connectedness=s.get("social_connectedness"),
                forward_momentum=s.get("forward_momentum"),
                key_events=s.get("key_events", []),
                questions_raised=s.get("questions_raised", []),
                entities=s.get("entities", {}),
                # New fast-filter fields
                overall_vibe=s.get("overall_vibe"),
                time_of_day_texture=s.get("time_of_day_texture"),
                # Machine-readable index
                peak_moment=s.get("peak_moment"),
                scalar_metrics=s.get("scalar_metrics"),
                narrative_threads=s.get("narrative_threads"),
                significant_delta=s.get("significant_delta"),
                physiological_flags=s.get("physiological_flags"),
                relational_map=s.get("relational_map"),
                entity_mentions=s.get("entity_mentions"),
                avoidance_signals=s.get("avoidance_signals"),
                growth_markers=s.get("growth_markers"),
                coping_mechanisms=s.get("coping_mechanisms"),
                self_perception_snapshot=s.get("self_perception_snapshot"),
                values_in_tension=s.get("values_in_tension"),
                context_bridge=s.get("context_bridge"),
                # Metadata
                thinking_trace=response.parsed.thinking_trace,
                source_units=[s.period_start for s in group],
                source_entry_count=sum(getattr(s, "source_entry_count", 0) for s in group),
                token_count_input=response.input_tokens or est.estimated_tokens,
                token_count_output=response.output_tokens,
                model_used=self.client.model_name,
                mode=self.config.mode,
                processing_time_seconds=round(response.elapsed_seconds, 2),
                timestamp_processed=datetime.now().isoformat(timespec="seconds"),
            )
            path = self.progress.save_unit(summary_unit)
            tok_in = response.input_tokens if response.input_tokens is not None else "?"
            tok_out = response.output_tokens if response.output_tokens is not None else "?"
            self.log(f"  [{idx + 1}/{total}] ✓ {path.name} (Tokens in: {tok_in}, Tokens out: {tok_out} tokens, Duration {response.elapsed_seconds:.1f}s)")
            results.append(summary_unit)

        return results

    def synthesize_entity(self, label: str, timeline: list[dict]) -> str:
        """
        Run the entity synthesis prompt and return the model's analysis text.
        Includes context-safety logic to truncate long timelines if needed.
        """
        import json 
        
        system_prompt = "You are a thoughtful therapist and historian. Be honest and insightful."
        reserved = self.config.output_budget + 5000
        safety_limit = self.config.context_window - reserved
        
        items = [f"[{e['period_start']}]: {e['note']}" for e in timeline]
        
        while True:
            timeline_text = "\n\n".join(items) if items else "(none)"
            user_prompt = self.config.template("entity_synthesis").format(
                entity=label,
                entity_timeline=timeline_text,
            )
            
            est = estimate_tokens(system_prompt + "\n" + user_prompt, self.config.context_window, reserved)
            if est.estimated_tokens > safety_limit and len(items) > 0:
                self.log(f"    ⚠ Entity timeline ({est.estimated_tokens} tokens) exceeds safety limit. Dropping oldest mention.")
                items.pop(0)
            else:
                break

        est = estimate_tokens(user_prompt, self.config.context_window, reserved)
        self.log(f"  → Synthesizing entity '{label}' (~{est.estimated_tokens} tokens)")

        response = self.client.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            estimated_input_tokens=est.estimated_tokens,
        )
        return response.parsed.clean_text
