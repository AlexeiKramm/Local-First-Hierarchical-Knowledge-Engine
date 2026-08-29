"""
tab_config.py
=============
Tab 1 — Input & Configuration.

Lets the user:
  - Pick the unified JSON input file
  - Set a date range via calendar date pickers (tkcalendar DateEntry)
  - Choose summarization mode (A — Isolated, or B — Historical)
  - Set the history window N slider
  - Choose which hierarchy levels to compute
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from .app import DARK


def _project_root() -> Path:
    """Return the project root (the inner diary_core git repo directory)."""
    # This file is at: <root>/diary_core/gui/tab_config.py
    return Path(__file__).resolve().parents[3]


# tkcalendar is an optional dependency. Fall back to plain text entries if not installed.
# Install with: pip install tkcalendar
try:
    from tkcalendar import DateEntry as _DateEntry
    _TKCAL_AVAILABLE = True
except ImportError:
    _TKCAL_AVAILABLE = False


class ConfigTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._build()

    def _build(self):
        canvas = tk.Canvas(self, bg=DARK["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self._scroll_frame = ttk.Frame(canvas)
        self._scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self._scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))

        p = self._scroll_frame

        # ── Input file ────────────────────────────────────────────────────────
        in_frame = ttk.LabelFrame(p, text="Input File (merged JSON)", padding=10)
        in_frame.pack(fill="x", padx=14, pady=(14, 6))

        self.input_var = tk.StringVar(
            value=str(_project_root() / "data" / "raw_data" / "processed" / "merged_diary.json")
        )
        ttk.Entry(in_frame, textvariable=self.input_var, width=60).pack(side="left", fill="x", expand=True)
        ttk.Button(in_frame, text="Browse…", command=self._browse_input).pack(side="left", padx=(8, 0))

        # ── Date range ────────────────────────────────────────────────────────
        date_frame = ttk.LabelFrame(p, text="Date Range", padding=10)
        date_frame.pack(fill="x", padx=14, pady=(0, 6))

        self.all_data_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            date_frame, text="Use all available data",
            variable=self.all_data_var,
            command=self._toggle_date_range,
            bg=DARK["bg"], fg=DARK["fg"], selectcolor=DARK["bg3"],
            activebackground=DARK["bg"], activeforeground=DARK["accent"],
            font=("Helvetica", 10),
        ).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 6))

        tk.Label(date_frame, text="From:", bg=DARK["bg"], fg=DARK["fg"]).grid(row=1, column=0, sticky="w")
        if _TKCAL_AVAILABLE:
            self._date_from_picker = _DateEntry(
                date_frame, width=12, date_pattern="yyyy-mm-dd",
                state="disabled",
                background=DARK["accent"], foreground=DARK["bg"],
                selectbackground=DARK["accent2"], selectforeground=DARK["fg"],
            )
            self._date_from_picker.grid(row=1, column=1, padx=(4, 16), sticky="w")
            self.date_from_var = tk.StringVar()

            def _update_from(*_):
                self.date_from_var.set(self._date_from_picker.get_date().strftime("%Y-%m-%d"))
            self._date_from_picker.bind("<<DateEntrySelected>>", _update_from)
        else:
            self.date_from_var = tk.StringVar(value="2025-01-01")
            self._date_from_picker = ttk.Entry(date_frame, textvariable=self.date_from_var, width=14, state="disabled")
            self._date_from_picker.grid(row=1, column=1, padx=(4, 16), sticky="w")

        tk.Label(date_frame, text="To:", bg=DARK["bg"], fg=DARK["fg"]).grid(row=1, column=2, sticky="w")
        if _TKCAL_AVAILABLE:
            self._date_to_picker = _DateEntry(
                date_frame, width=12, date_pattern="yyyy-mm-dd",
                state="disabled",
                background=DARK["accent"], foreground=DARK["bg"],
                selectbackground=DARK["accent2"], selectforeground=DARK["fg"],
            )
            self._date_to_picker.grid(row=1, column=3, padx=4, sticky="w")
            self.date_to_var = tk.StringVar()

            def _update_to(*_):
                self.date_to_var.set(self._date_to_picker.get_date().strftime("%Y-%m-%d"))
            self._date_to_picker.bind("<<DateEntrySelected>>", _update_to)
        else:
            self.date_to_var = tk.StringVar(value="2025-12-31")
            self._date_to_picker = ttk.Entry(date_frame, textvariable=self.date_to_var, width=14, state="disabled")
            self._date_to_picker.grid(row=1, column=3, padx=4, sticky="w")

        cal_note = "(tkcalendar not installed — using text entry. pip install tkcalendar)" if not _TKCAL_AVAILABLE else "(click to open calendar)"
        tk.Label(date_frame, text=cal_note, bg=DARK["bg"], fg=DARK["fg2"],
                 font=("Helvetica", 8, "italic")).grid(row=1, column=4, padx=(10, 0), sticky="w")

        # ── Summarization mode ────────────────────────────────────────────────
        modes_frame = ttk.LabelFrame(p, text="Summarization Mode", padding=10)
        modes_frame.pack(fill="x", padx=14, pady=(0, 6))

        self.mode_var = tk.StringVar(value="historical")

        for label, val, note in [
            ("Mode A — Isolated  (no context, fastest — every day is self-contained)", "isolated",
             "Each day uses only its own raw entries. Best for initial processing."),
            ("Mode B — Historical  (previous N raw days as context)", "historical",
             "Each day receives the previous N days of raw entries as background context."),
        ]:
            row_frame = ttk.Frame(modes_frame)
            row_frame.pack(fill="x", anchor="w", pady=2)
            tk.Radiobutton(
                row_frame, text=label, variable=self.mode_var, value=val,
                bg=DARK["bg"], fg=DARK["fg"], selectcolor=DARK["bg3"],
                activebackground=DARK["bg"], activeforeground=DARK["accent"],
                font=("Helvetica", 10),
                command=self._update_mode_ui,
            ).pack(anchor="w")
            tk.Label(row_frame, text=f"  ↳ {note}", bg=DARK["bg"], fg=DARK["fg2"],
                     font=("Helvetica", 8, "italic")).pack(anchor="w")

        # ── Context window ────────────────────────────────────────────────────
        sliders_frame = ttk.LabelFrame(p, text="Context Settings", padding=10)
        sliders_frame.pack(fill="x", padx=14, pady=(0, 6))

        self.history_n_var = tk.IntVar(value=7)
        self._make_slider(
            sliders_frame,
            "History window N (Mode B — number of previous raw days shown as context):",
            self.history_n_var, 0, 7, row=0
        )

        tk.Label(sliders_frame,
                 text="Context format: Raw full entries (maximized) — always on",
                 bg=DARK["bg"], fg=DARK["fg2"], font=("Helvetica", 8, "italic")
                 ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # ── Hierarchy levels ──────────────────────────────────────────────────
        hier_frame = ttk.LabelFrame(p, text="Hierarchical Levels to Compute", padding=10)
        hier_frame.pack(fill="x", padx=14, pady=(0, 6))

        self.level_day_var   = tk.BooleanVar(value=True)
        self.level_week_var  = tk.BooleanVar(value=True)
        self.level_month_var = tk.BooleanVar(value=True)
        self.level_year_var  = tk.BooleanVar(value=True)

        for txt, var in [("Days", self.level_day_var), ("Weeks", self.level_week_var),
                         ("Months", self.level_month_var), ("Year", self.level_year_var)]:
            tk.Checkbutton(
                hier_frame, text=txt, variable=var,
                bg=DARK["bg"], fg=DARK["fg"], selectcolor=DARK["bg3"],
                activebackground=DARK["bg"], activeforeground=DARK["accent"],
                font=("Helvetica", 10),
            ).pack(side="left", padx=8)

        self._update_mode_ui()

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _update_mode_ui(self):
        """Enable/disable the history slider based on selected mode."""
        pass  # slider is always visible; just a visual reminder

    def _make_slider(self, parent, label: str, var: tk.IntVar, from_: int, to: int, row: int):
        tk.Label(parent, text=label, bg=DARK["bg"], fg=DARK["fg"],
                 font=("Helvetica", 9)).grid(row=row, column=0, sticky="w", pady=2)
        tk.Scale(parent, from_=from_, to=to, orient="horizontal", variable=var,
                 bg=DARK["bg"], fg=DARK["fg"], troughcolor=DARK["bg3"],
                 activebackground=DARK["accent"], highlightthickness=0,
                 length=200).grid(row=row, column=1, padx=10, sticky="w")
        tk.Label(parent, textvariable=var, bg=DARK["bg"], fg=DARK["accent"],
                 font=("Helvetica", 10, "bold"), width=3).grid(row=row, column=2, sticky="w")

    def _browse_input(self):
        path = filedialog.askopenfilename(
            title="Select merged diary JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.input_var.set(path)

    def _toggle_date_range(self):
        state = "disabled" if self.all_data_var.get() else "normal"
        if _TKCAL_AVAILABLE:
            self._date_from_picker.config(state=state)
            self._date_to_picker.config(state=state)
        else:
            self._date_from_picker.config(state=state)
            self._date_to_picker.config(state=state)

    # ── Data accessors (used by RunTab) ───────────────────────────────────────

    def get_date_range(self) -> tuple[str, str] | None:
        """Return (start, end) date strings, or None if 'all data' is selected."""
        if self.all_data_var.get():
            return None
        if _TKCAL_AVAILABLE:
            start = self._date_from_picker.get_date().strftime("%Y-%m-%d")
            end = self._date_to_picker.get_date().strftime("%Y-%m-%d")
        else:
            start = self.date_from_var.get().strip()
            end = self.date_to_var.get().strip()
        if start and end:
            return start, end
        return None

    def get_mode(self) -> str:
        return self.mode_var.get()

    def get_levels(self) -> tuple[str, ...]:
        levels = []
        if self.level_day_var.get():   levels.append("day")
        if self.level_week_var.get():  levels.append("week")
        if self.level_month_var.get(): levels.append("month")
        if self.level_year_var.get():  levels.append("year")
        return tuple(levels)
