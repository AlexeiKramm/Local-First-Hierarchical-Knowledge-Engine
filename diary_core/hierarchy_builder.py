"""
hierarchy_builder.py
====================
Orchestrates the full multi-level summarization pipeline:
  raw entries → days → weeks → months → year

Each level reads only from the level below it.
The base unit is always "day" (week/day selection removed — always day-first).
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from .input_loader import DayEntries, group_into_months, group_into_weeks
from .progress_manager import ProgressManager
from .schema import SummaryUnit
from .summarizer import Summarizer, SummarizationConfig
from .llm_client import LLMClient


LogCallback = Callable[[str], None]


class HierarchyBuilder:
    """
    High-level orchestrator for the full diary summarization pipeline.

    Usage:
        builder = HierarchyBuilder(client, progress, config, log_fn, pause_event, stop_event)
        results = builder.run(days, levels=("day","week","month","year"))
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
        self._summarizer = Summarizer(
            client=client,
            progress=progress,
            config=config,
            log=log,
            pause_event=pause_event,
            stop_event=stop_event,
        )

    def _stopped(self) -> bool:
        return bool(self.stop_event and self.stop_event.is_set())

    # ── Main pipeline ──────────────────────────────────────────────────────

    def run(
        self,
        days: list[DayEntries],
        levels: tuple[str, ...] = ("day", "week", "month", "year"),
        target_range: Optional[tuple[str, str]] = None,
        cascade_updates: bool = True,
    ) -> dict[str, list[SummaryUnit]]:
        """
        Run the full pipeline for the selected levels.

        Args:
            days:         Pre-loaded, full list of DayEntries.
            levels:       Which levels to compute. Defaults to all four.
            target_range: Absolute date boundaries to generate summaries for.
            cascade_updates: If True, clearing cache for upper levels when underlying days are newly generated.
        """
        results: dict[str, list[SummaryUnit]] = {}
        new_day_keys: set[str] = set()

        # ── Day level ──────────────────────────────────────────────────────
        if "day" in levels:
            self.log("\n═══ DAY SUMMARIES ═══")

            use_parallel = self.config.concurrency > 1
            if use_parallel:
                self.log(f"  Parallel mode: {self.config.concurrency} concurrent requests")
                day_summaries, new_day_keys = self._summarizer.summarize_days_parallel(
                    days=days,
                    target_range=target_range,
                )
            else:
                day_summaries, new_day_keys = self._summarizer.summarize_days(
                    days=days,
                    target_range=target_range,
                )
            results["day"] = day_summaries
        else:
            # Load existing day summaries if available (needed for higher levels)
            day_summaries = self.progress.load_all_units("day")
            if day_summaries:
                self.log(f"  Loaded {len(day_summaries)} existing day summaries.")
                results["day"] = day_summaries
            else:
                day_summaries = []

        if self._stopped():
            return results

        # ── Cascading Invalidation ─────────────────────────────────────────
        if cascade_updates and new_day_keys:
            from datetime import datetime, timedelta

            # Build a lookup: iso-week-string → actual period_start key used when saving
            # Week summaries are saved with period_start = first day of the week (Monday),
            # NOT the ISO week string — so we must resolve the correct filename key.
            week_key_lookup: dict[str, str] = {}  # "YYYY-Www" → period_start (e.g. "2023-01-30")
            for ws in self.progress.load_all_units("week"):
                try:
                    ws_dt = datetime.strptime(ws.period_start, "%Y-%m-%d")
                    yw, ww, _ = ws_dt.isocalendar()
                    week_key_lookup[f"{yw}-W{ww:02d}"] = ws.period_start
                except ValueError:
                    pass

            affected_week_keys: set[str] = set()  # period_start dates (actual filenames)
            affected_months: set[str] = set()      # YYYY-MM keys
            affected_years: set[str] = set()       # YYYY keys

            for date_str in new_day_keys:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    y, w, _ = dt.isocalendar()
                    iso_week = f"{y}-W{w:02d}"
                    if iso_week in week_key_lookup:
                        affected_week_keys.add(week_key_lookup[iso_week])
                    else:
                        # Fallback: compute Monday of the ISO week
                        monday = dt - timedelta(days=dt.weekday())
                        affected_week_keys.add(monday.strftime("%Y-%m-%d"))
                    affected_months.add(date_str[:7])
                    affected_years.add(str(dt.year))
                except ValueError:
                    pass

            invalidated_weeks = sum(1 for wk in affected_week_keys if self.progress.invalidate_unit("week", wk))
            invalidated_months = sum(1 for m in affected_months if self.progress.invalidate_unit("month", m))
            invalidated_years = sum(1 for y in affected_years if self.progress.invalidate_unit("year", y))

            if invalidated_weeks or invalidated_months or invalidated_years:
                self.log(f"  [Cascade] Invalidated {invalidated_weeks} week(s), {invalidated_months} month(s), {invalidated_years} year(s) — will be regenerated.")

        # ── Week level ─────────────────────────────────────────────────────
        if "week" in levels:
            self.log("\n═══ WEEK SUMMARIES ═══")
            day_sum = results.get("day", self.progress.load_all_units("day"))
            week_groups = _group_summaries_by_week(day_sum)

            # Pass raw days as grandchild data for weeks (raw entries are 2 levels below week)
            grandchild_dict = {d.date: d for d in days} if days and self.config.inject_grandchild_data else {}

            week_summaries = self._summarizer.summarize_higher_level(
                unit="week", groups=week_groups,
                prior_summaries=[],
                grandchild_dict=grandchild_dict or None,
                inject_grandchild_data=self.config.inject_grandchild_data,
            )
            results["week"] = week_summaries
        if self._stopped():
            return results

        # ── Month level ────────────────────────────────────────────────────
        if "month" in levels:
            self.log("\n═══ MONTH SUMMARIES ═══")
            week_sum = results.get("week", self.progress.load_all_units("week"))
            day_sum = results.get("day", self.progress.load_all_units("day"))

            month_groups = _group_summaries_by_month(week_sum)
            # Day summaries are the grandchild data for months (2 levels below)
            grandchild_dict = {d.period_start: d for d in day_sum} if day_sum and self.config.inject_grandchild_data else {}
            month_summaries = self._summarizer.summarize_higher_level(
                unit="month", groups=month_groups,
                prior_summaries=[],
                grandchild_dict=grandchild_dict or None,
                inject_grandchild_data=self.config.inject_grandchild_data,
            )
            results["month"] = month_summaries
        if self._stopped():
            return results

        # ── Year level ─────────────────────────────────────────────────────
        if "year" in levels:
            self.log("\n═══ YEAR SUMMARIES ═══")
            month_sum = results.get("month", self.progress.load_all_units("month"))
            week_sum = results.get("week", self.progress.load_all_units("week"))
            # Week summaries are the grandchild data for years (2 levels below)
            week_dict = {w.period_start: w for w in week_sum} if week_sum and self.config.inject_grandchild_data else {}
            # Group months by calendar year — produces one summary per year
            year_groups = _group_summaries_by_year(month_sum)
            year_summaries = self._summarizer.summarize_higher_level(
                unit="year", groups=year_groups,
                prior_summaries=[],
                grandchild_dict=week_dict or None,
                inject_grandchild_data=self.config.inject_grandchild_data,
            )
            results["year"] = year_summaries


        self.log("\n✓ Pipeline complete.")
        return results


# ─────────────────────────────────────────────
#  Grouping helpers for summary units
# ─────────────────────────────────────────────

def _group_summaries_by_week(summaries: list[SummaryUnit]) -> list[list[SummaryUnit]]:
    """Group SummaryUnit day records into ISO calendar weeks."""
    from itertools import groupby
    from datetime import datetime

    def iso_week(s: SummaryUnit) -> str:
        dt = datetime.strptime(s.period_start, "%Y-%m-%d")
        y, w, _ = dt.isocalendar()
        return f"{y}-W{w:02d}"

    return [list(g) for _, g in groupby(
        sorted(summaries, key=lambda s: s.period_start), key=iso_week
    )]


def _group_summaries_by_month(summaries: list[SummaryUnit]) -> list[list[SummaryUnit]]:
    """Group SummaryUnit week records into calendar months."""
    from itertools import groupby

    def month_key(s: SummaryUnit) -> str:
        return s.period_start[:7]

    return [list(g) for _, g in groupby(
        sorted(summaries, key=lambda s: s.period_start), key=month_key
    )]


def _group_summaries_by_year(summaries: list[SummaryUnit]) -> list[list[SummaryUnit]]:
    """Group SummaryUnit month records into calendar years.
    Returns one group per year, e.g. [[jan-dec 2022], [jan-dec 2023], ...].
    Each group is passed to summarize_higher_level as an independent year summary.
    """
    from itertools import groupby

    def year_key(s: SummaryUnit) -> str:
        return s.period_start[:4]  # "2023-01" → "2023"

    return [list(g) for _, g in groupby(
        sorted(summaries, key=lambda s: s.period_start), key=year_key
    )]

