"""
tab_owui.py
===========
Tab 1 — OWUI Sources

Lets the user add raw OWUI export JSON files, preview conversations,
include/exclude individual conversations, then stage entries for assembly.
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
    # This file is at: <root>/diary_ingestor/gui/tab_owui.py
    return Path(__file__).resolve().parents[3]


class OWUITab(ttk.Frame):
    def __init__(self, parent, app: "IngestorApp"):
        super().__init__(parent)
        self.app = app
        self._sources: list[str] = []               # loaded file paths
        self._conv_data: dict[str, list[dict]] = {}  # filepath → [conv dicts]
        self._include_vars: dict[str, dict[str, tk.BooleanVar]] = {}  # filepath → {title → var}
        self._current_file: str = ""
        self._build()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build(self):
        main = ttk.Frame(self)
        main.pack(fill="both", expand=True, padx=12, pady=12)

        # Top: file list + add/remove
        files_frame = ttk.LabelFrame(main, text="OWUI Export Files", padding=8)
        files_frame.pack(fill="x", pady=(0, 8))

        btn_row = ttk.Frame(files_frame)
        btn_row.pack(fill="x", pady=(0, 4))
        ttk.Button(btn_row, text="➕ Add File(s)…", command=self._add_files).pack(side="left")
        ttk.Button(btn_row, text="🗑 Remove Selected", command=self._remove_file).pack(side="left", padx=6)

        self._files_list = tk.Listbox(
            files_frame, bg=DARK["bg2"], fg=DARK["fg"],
            font=("Courier", 9), relief="flat", height=4,
            selectbackground=DARK["accent"], selectforeground=DARK["bg"],
        )
        self._files_list.pack(fill="x")
        self._files_list.bind("<<ListboxSelect>>", self._on_file_select)

        # Middle: conversation list with checkboxes
        conv_frame = ttk.LabelFrame(main, text="Conversations (check to include)", padding=8)
        conv_frame.pack(fill="both", expand=True, pady=(0, 8))

        conv_inner = ttk.Frame(conv_frame)
        conv_inner.pack(fill="both", expand=True)

        self._conv_canvas = tk.Canvas(conv_inner, bg=DARK["bg2"], highlightthickness=0)
        self._conv_canvas.pack(side="left", fill="both", expand=True)
        conv_scroll = ttk.Scrollbar(conv_inner, orient="vertical", command=self._conv_canvas.yview)
        conv_scroll.pack(side="right", fill="y")
        self._conv_canvas.configure(yscrollcommand=conv_scroll.set)

        self._conv_frame_inner = ttk.Frame(self._conv_canvas)
        self._conv_canvas.create_window((0, 0), window=self._conv_frame_inner, anchor="nw")
        self._conv_frame_inner.bind("<Configure>",
                                    lambda e: self._conv_canvas.configure(
                                        scrollregion=self._conv_canvas.bbox("all")))

        # Select all / none
        sel_row = ttk.Frame(conv_frame)
        sel_row.pack(fill="x", pady=(4, 0))
        ttk.Button(sel_row, text="✓ All", command=self._select_all).pack(side="left")
        ttk.Button(sel_row, text="✗ None", command=self._select_none).pack(side="left", padx=6)
        self._conv_count_var = tk.StringVar(value="No file loaded")
        tk.Label(sel_row, textvariable=self._conv_count_var,
                 bg=DARK["bg"], fg=DARK["subtext"], font=("Helvetica", 9)).pack(side="left", padx=8)

        # Bottom: controls
        ctrl = ttk.Frame(main)
        ctrl.pack(fill="x")
        ttk.Button(ctrl, text="▶ Stage Selected Conversations", command=self._stage).pack(side="left")
        self._log_text = scrolledtext.ScrolledText(
            main, wrap="word", bg=DARK["bg2"], fg=DARK["fg"],
            font=("Courier", 9), relief="flat", state="disabled", height=6,
        )
        self._log_text.pack(fill="x", pady=(8, 0))

    # ── Actions ───────────────────────────────────────────────────────────

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select OWUI export JSON file(s)",
            initialdir=str(_project_root() / "data" / "raw_data"),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        for p in paths:
            if p not in self._sources:
                self._sources.append(p)
                self._files_list.insert(tk.END, Path(p).name)
                self._log(f"Added: {p}")

    def _remove_file(self):
        sel = self._files_list.curselection()
        if not sel:
            return
        idx = sel[0]
        path = self._sources[idx]
        self._sources.pop(idx)
        self._files_list.delete(idx)
        self._conv_data.pop(path, None)
        self._include_vars.pop(path, None)
        self._clear_conv_list()
        self._log(f"Removed: {path}")

    def _on_file_select(self, _event=None):
        sel = self._files_list.curselection()
        if not sel:
            return
        path = self._sources[sel[0]]
        self._current_file = path
        if path not in self._conv_data:
            self._load_conversations(path)
        else:
            self._populate_conv_list(path)

    def _load_conversations(self, path: str):
        self._log(f"Scanning conversations in {Path(path).name}…")
        from ..parsers.owui_parser import list_conversations
        try:
            convs = list_conversations(path)
        except Exception as exc:
            messagebox.showerror("Load error", str(exc))
            return
        self._conv_data[path] = convs
        self._include_vars[path] = {}
        self._populate_conv_list(path)
        self._log(f"  Found {len(convs)} conversations")

    def _populate_conv_list(self, path: str):
        self._clear_conv_list()
        convs = self._conv_data.get(path, [])
        vars_dict = self._include_vars.get(path, {})
        self._conv_count_var.set(f"{len(convs)} conversations")

        for c in convs:
            title = c["title"]
            if title not in vars_dict:
                vars_dict[title] = tk.BooleanVar(value=True)

            row = ttk.Frame(self._conv_frame_inner)
            row.pack(fill="x", pady=1)
            ttk.Checkbutton(row, variable=vars_dict[title],
                            text=f"[{c['date_start']:^10}] ({c['message_count']:>3} msgs)  {title[:60]}",
                            ).pack(anchor="w")

        self._include_vars[path] = vars_dict

    def _clear_conv_list(self):
        for w in self._conv_frame_inner.winfo_children():
            w.destroy()

    def _select_all(self):
        if not self._current_file:
            return
        for var in self._include_vars.get(self._current_file, {}).values():
            var.set(True)

    def _select_none(self):
        if not self._current_file:
            return
        for var in self._include_vars.get(self._current_file, {}).values():
            var.set(False)

    def _stage(self):
        if not self._sources:
            messagebox.showwarning("No files", "Please add OWUI export file(s) first.")
            return
        threading.Thread(target=self._stage_worker, daemon=True).start()

    def _stage_worker(self):
        from ..parsers.owui_parser import parse_owui_file
        total_entries = []
        for path in self._sources:
            include_titles: set[str] | None = None
            if path in self._include_vars:
                include_titles = {title for title, var in self._include_vars[path].items() if var.get()}
            self._log(f"Processing {Path(path).name}…")
            try:
                entries = parse_owui_file(path, log=self._log, include_titles=include_titles)
                total_entries.extend(entries)
                self._log(f"  → {len(entries)} entries staged")
            except Exception as exc:
                self._log(f"  ✗ Error: {exc}")

        if total_entries:
            self.app.stage_entries(total_entries, f"OWUI ({len(self._sources)} files)")
            self._log(f"\n✓ Total staged: {len(total_entries)} entries")
        else:
            self._log("No entries found to stage.")

    # ── Log helpers ───────────────────────────────────────────────────────

    def _log(self, msg: str):
        self._log_text.configure(state="normal")
        self._log_text.insert("end", msg + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")
        self.update_idletasks()
