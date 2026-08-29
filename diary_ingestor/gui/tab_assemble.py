"""
tab_assemble.py
===============
Tab 4 — Assemble
Combines all staged entries into raw_entries/ per-day files and merged JSON.

Shows a live breakdown of what's been staged by source, so the user can see
at a glance which tabs have contributed before hitting Assemble.
"""
from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import TYPE_CHECKING

from .app import DARK

if TYPE_CHECKING:
    from .app import IngestorApp


def _project_root() -> Path:
    """Return the project root (the inner diary_core git repo directory)."""
    # This file is at: <root>/diary_ingestor/gui/tab_assemble.py
    return Path(__file__).resolve().parents[3]


class AssembleTab(ttk.Frame):
    def __init__(self, parent, app: "IngestorApp"):
        super().__init__(parent)
        self.app = app
        self._build()
        self._set_defaults()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build(self):
        main = ttk.Frame(self)
        main.pack(fill="both", expand=True, padx=12, pady=12)

        # ── Staged entries panel ───────────────────────────────────────────
        top = ttk.LabelFrame(main, text="Staged Entries", padding=8)
        top.pack(fill="x", pady=(0, 8))

        # Total count
        count_row = ttk.Frame(top)
        count_row.pack(fill="x", pady=(0, 4))
        self._staged_var = tk.StringVar(value="No entries staged yet")
        tk.Label(count_row, textvariable=self._staged_var, bg=DARK["bg"], fg=DARK["accent"],
                 font=("Helvetica", 12, "bold")).pack(side="left")
        ttk.Button(count_row, text="🗑 Clear All Staged", command=self._clear_staged).pack(side="right")

        # Per-source breakdown
        self._breakdown_text = scrolledtext.ScrolledText(
            top, wrap="word", bg=DARK["bg2"], fg=DARK["subtext"],
            font=("Courier", 9), relief="flat", state="disabled", height=4,
        )
        self._breakdown_text.pack(fill="x")

        # ── Output paths ───────────────────────────────────────────────────
        rf = ttk.LabelFrame(main, text="Output: raw_entries/ Directory", padding=8)
        rf.pack(fill="x", pady=(0, 6))
        self._raw_dir_var = tk.StringVar()
        ttk.Entry(rf, textvariable=self._raw_dir_var, width=60).pack(side="left", fill="x", expand=True)
        ttk.Button(rf, text="Browse…", command=self._browse_raw_dir).pack(side="left", padx=6)

        mf = ttk.LabelFrame(main, text="Output: Merged JSON File (optional)", padding=8)
        mf.pack(fill="x", pady=(0, 6))
        self._merged_var = tk.StringVar()
        ttk.Entry(mf, textvariable=self._merged_var, width=60).pack(side="left", fill="x", expand=True)
        ttk.Button(mf, text="Browse…", command=self._browse_merged).pack(side="left", padx=6)

        # ── Options ────────────────────────────────────────────────────────
        opt_frame = ttk.Frame(main)
        opt_frame.pack(fill="x", pady=(0, 4))
        self._clear_after_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt_frame,
            text="Clear staged entries after assembly (uncheck to keep them for re-runs)",
            variable=self._clear_after_var,
        ).pack(anchor="w")

        # ── Progress + controls ────────────────────────────────────────────
        pf = ttk.Frame(main)
        pf.pack(fill="x", pady=6)
        self._progress = ttk.Progressbar(pf, mode="indeterminate", length=400)
        self._progress.pack(side="left", fill="x", expand=True)

        btn_row = ttk.Frame(main)
        btn_row.pack(fill="x", pady=4)
        ttk.Button(btn_row, text="▶ Assemble Now", command=self._run_assemble).pack(side="left")

        # ── Log ────────────────────────────────────────────────────────────
        lf = ttk.LabelFrame(main, text="Log", padding=8)
        lf.pack(fill="both", expand=True, pady=(8, 0))
        self._log_text = scrolledtext.ScrolledText(
            lf, wrap="word", bg=DARK["bg2"], fg=DARK["fg"],
            font=("Courier", 9), relief="flat", state="disabled")
        self._log_text.pack(fill="both", expand=True)

    # ── Defaults ──────────────────────────────────────────────────────────

    def _set_defaults(self):
        root = _project_root()
        if not self._raw_dir_var.get():
            self._raw_dir_var.set(str(root / "data" / "diary.db"))
        if not self._merged_var.get():
            self._merged_var.set(str(root / "data" / "raw_data" / "processed" / "merged_diary.json"))

    # ── State ─────────────────────────────────────────────────────────────

    def refresh_staged_count(self):
        staged = self.app.get_staged()
        n = len(staged)
        if n == 0:
            self._staged_var.set("No entries staged yet")
            self._set_breakdown("")
        else:
            self._staged_var.set(f"{n} entries ready to assemble")
            # Build per-source breakdown
            labels = self.app.get_staged_labels()
            lines = [f"  {label}: {count} entries" for label, count in labels]
            self._set_breakdown("\n".join(lines))

    def _set_breakdown(self, text: str):
        self._breakdown_text.configure(state="normal")
        self._breakdown_text.delete("1.0", "end")
        if text:
            self._breakdown_text.insert("1.0", text)
        self._breakdown_text.configure(state="disabled")

    def _clear_staged(self):
        if messagebox.askyesno("Clear staged", "Remove all staged entries?"):
            self.app.clear_staged()

    # ── Browse ────────────────────────────────────────────────────────────

    def _browse_raw_dir(self):
        d = filedialog.askdirectory(title="Select raw_entries/ directory")
        if d:
            self._raw_dir_var.set(d)
            # Auto-suggest merged JSON path next to it
            if not self._merged_var.get():
                self._merged_var.set(str(Path(d).parent / "merged_diary.json"))

    def _browse_merged(self):
        f = filedialog.asksaveasfilename(
            title="Save merged diary JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
        )
        if f:
            self._merged_var.set(f)

    # ── Assembly ──────────────────────────────────────────────────────────

    def _run_assemble(self):
        raw_dir = self._raw_dir_var.get().strip()
        if not raw_dir:
            messagebox.showerror("Error", "Please select the raw_entries/ output directory.")
            return
        staged = self.app.get_staged()
        if not staged:
            messagebox.showerror("Error", "No entries staged. Use the other tabs to stage entries first.")
            return
        threading.Thread(target=self._assemble_worker, args=(raw_dir, staged), daemon=True).start()

    def _assemble_worker(self, raw_dir: str, staged: list[dict]):
        from ..assembler import Assembler
        self._progress.start(10)
        self._log("Starting assembly…")
        self.app.set_status("Assembling entries…")

        assembler = Assembler(raw_dir, log=self._log)
        result = assembler.assemble(staged)

        self._log(
            f"\n✓ Assembly complete:\n"
            f"  New date files:     {result['new_dates']}\n"
            f"  Entries appended:   {result['appended']}\n"
            f"  Duplicates skipped: {result['skipped_duplicates']}"
        )

        merged_path = self._merged_var.get().strip()
        if merged_path:
            self._log(f"\nRegenerating merged JSON → {merged_path}")
            total = assembler.regenerate_merged(merged_path)
            self._log(f"✓ Merged JSON saved — {total} total entries")

        self._progress.stop()
        self.app.set_status("Assembly complete!")

        # Only clear staged if the option is checked
        if self._clear_after_var.get():
            self.app.clear_staged()

        messagebox.showinfo(
            "Done",
            f"Assembly complete!\n"
            f"  {result['appended']} entries written to {raw_dir}\n"
            f"  {result['skipped_duplicates']} duplicates skipped"
        )

    def _log(self, msg: str):
        self._log_text.configure(state="normal")
        self._log_text.insert("end", msg + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")
        self.update_idletasks()
