"""
tab_old_diary.py
================
Tab 3 — Legacy Text Diary Parser

Modes:
  LLM Scan  — select .txt file, scan with LLM (checkpoint save-state).
              After a successful scan the result is saved as a cache JSON
              ({stem}_parsed_cache.json) in the same folder as the source file.
              On next load, that cache is detected and can be loaded directly.

  Pre-parsed — skip all LLM work. Browse to any JSON file in the format
              [{datetime_raw, datetime_parsed, user, response}] and load it
              straight into the Review tab.

Sub-tabs:
  Parse  — mode selection + scan controls
  Review — inspect and correct detected entries before staging
"""
from __future__ import annotations

import json
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
    # This file is at: <root>/diary_ingestor/gui/tab_old_diary.py
    return Path(__file__).resolve().parents[3]


def _cache_dir() -> Path:
    return _project_root() / "data" / "raw_data" / "cache"


# Suffix used when saving the parsed-entries cache (kept next to source file for GUI convenience)
_CACHE_SUFFIX = "_parsed_cache.json"


class OldDiaryTab(ttk.Frame):
    def __init__(self, parent, app: "IngestorApp"):
        super().__init__(parent)
        self.app = app
        self._filepath: str = ""
        self._raw_lines: list[str] = []
        self._anchors: list[dict] = []   # [{line_index, datetime_parsed, line_text}]
        self._entries: list[dict] = []   # final grouped + resolved entries
        self._stop_event = threading.Event()
        self._build()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        self._parse_tab = ttk.Frame(nb)
        self._review_tab = ttk.Frame(nb)
        nb.add(self._parse_tab,  text="  Parse  ")
        nb.add(self._review_tab, text="  Review & Edit  ")

        self._build_parse(self._parse_tab)
        self._build_review(self._review_tab)

    # ── Parse sub-tab ─────────────────────────────────────────────────────

    def _build_parse(self, parent):
        # ── Mode selector ─────────────────────────────────────────────────
        mf = ttk.LabelFrame(parent, text="Mode", padding=8)
        mf.pack(fill="x", padx=12, pady=(12, 6))
        self._mode_var = tk.StringVar(value="llm")
        ttk.Radiobutton(
            mf, text="🤖  LLM Scan  (parse .txt file with a local/remote LLM)",
            variable=self._mode_var, value="llm",
            command=self._on_mode_change,
        ).pack(anchor="w")
        ttk.Radiobutton(
            mf, text="📄  Load Pre-parsed JSON  (bypass all LLM — select an already-parsed cache file)",
            variable=self._mode_var, value="preparsed",
            command=self._on_mode_change,
        ).pack(anchor="w", pady=(4, 0))

        # ── LLM Scan section ──────────────────────────────────────────────
        self._llm_section = ttk.Frame(parent)
        self._llm_section.pack(fill="both", expand=True)
        self._build_llm_section(self._llm_section)

        # ── Pre-parsed section ────────────────────────────────────────────
        self._preparsed_section = ttk.Frame(parent)
        # (not packed yet — shown on mode switch)
        self._build_preparsed_section(self._preparsed_section)

    def _build_llm_section(self, parent):
        # File selector
        ff = ttk.LabelFrame(parent, text="Legacy Diary File (.txt / .md)", padding=8)
        ff.pack(fill="x", padx=12, pady=(6, 6))
        self._file_var = tk.StringVar()
        ttk.Entry(ff, textvariable=self._file_var, width=55).pack(side="left", fill="x", expand=True)
        ttk.Button(ff, text="Browse…", command=self._browse_file).pack(side="left", padx=6)

        # Cache indicator
        cf = ttk.LabelFrame(parent, text="Parsed Cache (auto-saved after scan)", padding=8)
        cf.pack(fill="x", padx=12, pady=(0, 6))
        self._cache_status_var = tk.StringVar(value="(browse a .txt file to check for cache)")
        tk.Label(cf, textvariable=self._cache_status_var, bg=DARK["bg"], fg=DARK["cyan"],
                 font=("Courier", 9), anchor="w").pack(fill="x")
        self._load_cache_btn = ttk.Button(cf, text="⚡ Load from cache (skip LLM scan)",
                                          command=self._load_from_cache, state="disabled")
        self._load_cache_btn.pack(anchor="w", pady=(4, 0))

        # LLM config
        lf = ttk.LabelFrame(parent, text="LLM Configuration", padding=8)
        lf.pack(fill="x", padx=12, pady=(0, 6))
        self._backend_var = tk.StringVar(value="local")
        ttk.Radiobutton(lf, text="Local (llama.cpp)", variable=self._backend_var,
                        value="local").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(lf, text="OpenRouter", variable=self._backend_var,
                        value="openrouter").grid(row=0, column=1, sticky="w", padx=20)

        import os
        default_api_base = os.getenv("INGESTOR_API_BASE") or os.getenv("LLM_API_BASE", "http://localhost:8080")
        default_model = os.getenv("INGESTOR_MODEL") or os.getenv("LLM_MODEL", "local-model")
        default_api_key = os.getenv("INGESTOR_API_KEY") or os.getenv("OPENROUTER_API_KEY", "")

        tk.Label(lf, text="API Base:", bg=DARK["bg"], fg=DARK["fg"]).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._api_base_var = tk.StringVar(value=default_api_base)
        ttk.Entry(lf, textvariable=self._api_base_var, width=44).grid(row=1, column=1, columnspan=2, sticky="w")

        tk.Label(lf, text="Model name:", bg=DARK["bg"], fg=DARK["fg"]).grid(row=2, column=0, sticky="w")
        self._model_var = tk.StringVar(value=default_model)
        ttk.Entry(lf, textvariable=self._model_var, width=44).grid(row=2, column=1, columnspan=2, sticky="w")

        tk.Label(lf, text="API Key (OpenRouter):", bg=DARK["bg"], fg=DARK["fg"]).grid(row=3, column=0, sticky="w")
        self._apikey_var = tk.StringVar(value=default_api_key)
        ttk.Entry(lf, textvariable=self._apikey_var, width=44, show="*").grid(row=3, column=1, columnspan=2, sticky="w")
        ttk.Button(lf, text="Test Connection", command=self._test_conn).grid(row=1, column=3, rowspan=2, padx=10, sticky="ns")

        # Checkpoint
        cpf = ttk.LabelFrame(parent, text="Save-state", padding=8)
        cpf.pack(fill="x", padx=12, pady=(0, 6))
        self._force_rescan = tk.BooleanVar(value=False)
        ttk.Checkbutton(cpf, text="Force full rescan (ignores saved checkpoint)",
                        variable=self._force_rescan).pack(anchor="w")
        self._cp_status_var = tk.StringVar(value="No checkpoint")
        tk.Label(cpf, textvariable=self._cp_status_var, bg=DARK["bg"],
                 fg=DARK["subtext"], font=("Helvetica", 9)).pack(anchor="w")

        # Progress
        pf = ttk.Frame(parent)
        pf.pack(fill="x", padx=12, pady=4)
        self._progress = ttk.Progressbar(pf, mode="determinate", length=500)
        self._progress.pack(side="left", fill="x", expand=True)
        self._prog_label = tk.Label(pf, text="", bg=DARK["bg"], fg=DARK["fg"],
                                    font=("Helvetica", 9), width=12)
        self._prog_label.pack(side="left", padx=6)

        btn_row = ttk.Frame(parent)
        btn_row.pack(fill="x", padx=12, pady=4)
        ttk.Button(btn_row, text="▶ Scan for Date Headers", command=self._run_scan).pack(side="left")
        ttk.Button(btn_row, text="⏹ Stop", command=self._stop_scan).pack(side="left", padx=6)

        lf2 = ttk.LabelFrame(parent, text="Log", padding=8)
        lf2.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self._parse_log = scrolledtext.ScrolledText(
            lf2, wrap="word", bg=DARK["bg2"], fg=DARK["fg"],
            font=("Courier", 9), relief="flat", state="disabled",
        )
        self._parse_log.pack(fill="both", expand=True)

    def _build_preparsed_section(self, parent):
        ff = ttk.LabelFrame(parent, text="Pre-parsed JSON File", padding=8)
        ff.pack(fill="x", padx=12, pady=(6, 6))
        tk.Label(
            ff,
            text="Select a JSON file in [{datetime_parsed, user, response, ...}] format.\n"
                 "Both user and assistant entries will be staged. No LLM involved.",
            bg=DARK["bg"], fg=DARK["subtext"], font=("Helvetica", 9), justify="left",
        ).pack(anchor="w", pady=(0, 6))
        row = ttk.Frame(ff)
        row.pack(fill="x")
        self._preparsed_var = tk.StringVar()
        ttk.Entry(row, textvariable=self._preparsed_var, width=55).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self._browse_preparsed).pack(side="left", padx=6)

        btn_row = ttk.Frame(parent)
        btn_row.pack(fill="x", padx=12, pady=(4, 0))
        ttk.Button(btn_row, text="▶ Load Pre-parsed File",
                   command=self._run_load_preparsed).pack(side="left")
        self._pp_stage_btn = ttk.Button(btn_row, text="▶▶ Stage All Entries →",
                                        command=self._stage_preparsed, state="disabled")
        self._pp_stage_btn.pack(side="left", padx=(10, 0))

        # Summary panel shown after loading
        self._pp_summary_var = tk.StringVar(value="")
        self._pp_summary_label = tk.Label(
            parent, textvariable=self._pp_summary_var,
            bg=DARK["bg"], fg=DARK["green"], font=("Helvetica", 10, "bold"),
            justify="left", anchor="w",
        )
        self._pp_summary_label.pack(fill="x", padx=12, pady=(4, 0))

        lf2 = ttk.LabelFrame(parent, text="Log", padding=8)
        lf2.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self._preparsed_log = scrolledtext.ScrolledText(
            lf2, wrap="word", bg=DARK["bg2"], fg=DARK["fg"],
            font=("Courier", 9), relief="flat", state="disabled",
        )
        self._preparsed_log.pack(fill="both", expand=True)

    # ── Review sub-tab ────────────────────────────────────────────────────

    def _build_review(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="both", expand=True, padx=12, pady=12)

        # Left: entry list
        left = ttk.LabelFrame(top, text="Detected Entries", padding=6)
        left.pack(side="left", fill="y")

        self._entry_listbox = tk.Listbox(
            left, width=36, bg=DARK["bg2"], fg=DARK["fg"],
            font=("Courier", 9), relief="flat", height=20,
            selectbackground=DARK["accent"], selectforeground=DARK["bg"],
        )
        self._entry_listbox.pack(side="left", fill="y")
        _sb = ttk.Scrollbar(left, orient="vertical", command=self._entry_listbox.yview)
        _sb.pack(side="right", fill="y")
        self._entry_listbox.configure(yscrollcommand=_sb.set)
        self._entry_listbox.bind("<<ListboxSelect>>", self._on_entry_select)

        # Right: detail + date editor
        right = ttk.Frame(top)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        date_frame = ttk.LabelFrame(right, text="Parsed Date (YYYY-MM-DD or NONE)", padding=8)
        date_frame.pack(fill="x", pady=(0, 8))
        self._date_var = tk.StringVar()
        ttk.Entry(date_frame, textvariable=self._date_var, font=("Courier", 11)).pack(side="left", fill="x", expand=True)
        ttk.Button(date_frame, text="Apply", command=self._apply_date).pack(side="left", padx=6)

        text_frame = ttk.LabelFrame(right, text="Entry Text", padding=8)
        text_frame.pack(fill="both", expand=True)
        self._entry_text = scrolledtext.ScrolledText(
            text_frame, wrap="word", bg=DARK["bg2"], fg=DARK["fg"],
            font=("Courier", 9), relief="flat", state="disabled",
        )
        self._entry_text.pack(fill="both", expand=True)

        # Bottom controls
        bot = ttk.Frame(parent)
        bot.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(bot, text="▶ Stage Entries", command=self._stage).pack(side="left")
        self._entry_count_var = tk.StringVar(value="No entries loaded")
        tk.Label(bot, textvariable=self._entry_count_var, bg=DARK["bg"],
                 fg=DARK["subtext"], font=("Helvetica", 9)).pack(side="left", padx=8)

    # ── Mode toggle ───────────────────────────────────────────────────────

    def _on_mode_change(self):
        if self._mode_var.get() == "llm":
            self._preparsed_section.pack_forget()
            self._llm_section.pack(fill="both", expand=True)
        else:
            self._llm_section.pack_forget()
            self._preparsed_section.pack(fill="both", expand=True)

    # ── Cache helpers ─────────────────────────────────────────────────────

    def _cache_path_for(self, filepath: str) -> Path:
        return Path(filepath).with_suffix("") .parent / (Path(filepath).stem + _CACHE_SUFFIX)

    def _save_entries_cache(self, filepath: str, entries: list[dict]):
        """Save parsed entries to a cache JSON file (Gemini-cache format)."""
        from ..parsers.old_diary_parser import to_cache_format
        cache_path = self._cache_path_for(filepath)
        try:
            data = to_cache_format(entries)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._plog(f"✓ Cache saved → {cache_path.name}  (reload next time without LLM)")
        except Exception as exc:
            self._plog(f"⚠ Could not save cache: {exc}")

    def _load_from_cache(self):
        """Load entries from the detected cache file."""
        filepath = self._filepath
        cache_path = self._cache_path_for(filepath)
        threading.Thread(
            target=self._load_preparsed_worker,
            args=(str(cache_path), self._plog),
            daemon=True,
        ).start()

    # ── LLM Scan actions ─────────────────────────────────────────────────

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Select legacy diary file",
            initialdir=str(_project_root() / "data" / "raw_data"),
            filetypes=[("Text files", "*.txt *.md"), ("All files", "*.*")],
        )
        if path:
            self._filepath = path
            self._file_var.set(path)
            from ..progress_store import ProgressStore
            store = ProgressStore(str(_cache_dir()))
            cached = store.load_date_cache(path)
            if cached:
                self._cp_status_var.set(f"Checkpoint: {len(cached)} anchors cached")
            else:
                self._cp_status_var.set("No checkpoint")

            # Check for parsed cache
            cache_path = self._cache_path_for(path)
            if cache_path.exists():
                self._cache_status_var.set(f"✓ Cache found: {cache_path.name}")
                self._load_cache_btn.configure(state="normal")
            else:
                self._cache_status_var.set("No cache yet — run scan to create one")
                self._load_cache_btn.configure(state="disabled")

    def _test_conn(self):
        from ..llm_client import LLMClient
        c = LLMClient(api_base=self._api_base_var.get().strip(),
                      model_name=self._model_var.get().strip(),
                      api_key=self._apikey_var.get().strip() or None)
        try:
            messagebox.showinfo("Connection OK", c.test_connection())
        except Exception as exc:
            messagebox.showerror("Connection failed", str(exc))

    def _run_scan(self):
        if not self._filepath:
            messagebox.showerror("Error", "Please select a diary file first.")
            return
        self._stop_event.clear()
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _stop_scan(self):
        self._stop_event.set()

    def _scan_worker(self):
        from ..llm_client import LLMClient
        from ..parsers.old_diary_parser import scan_file, build_entries, resolve_undated
        from ..progress_store import ProgressStore

        path = self._filepath
        store = ProgressStore(str(_cache_dir()))

        if self._force_rescan.get():
            store.clear_date_cache(path)
            prior = []
            self._plog("Force rescan: cleared checkpoint.")
        else:
            prior = store.load_date_cache(path)
            if prior:
                self._plog(f"Resuming: {len(prior)} cached anchors.")

        client = LLMClient(
            api_base=self._api_base_var.get().strip(),
            model_name=self._model_var.get().strip(),
            api_key=self._apikey_var.get().strip() or None,
        )

        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self._raw_lines = lines
        total = len(lines)

        def on_progress(cur, tot, info):
            self._progress["maximum"] = tot
            self._progress["value"] = cur
            self._prog_label.config(text=f"{cur} / {tot}")
            self.update_idletasks()

        def on_checkpoint(anchors):
            store.save_date_cache(path, anchors)

        self._plog(f"Scanning {total} lines…")
        anchors = scan_file(
            path, client,
            prior_checkpoint=prior,
            on_progress=on_progress,
            on_checkpoint=on_checkpoint,
            stop_event=self._stop_event,
            log=self._plog,
        )
        self._anchors = anchors

        self._plog(f"\nFound {len(anchors)} dated entry anchors. Building entries…")
        entries = build_entries(lines, anchors)
        self._entries = resolve_undated(entries)

        self._plog(f"✓ {len(self._entries)} entries ready. Switch to 'Review & Edit' tab.")
        self.app.set_status(f"Old diary: {len(self._entries)} entries detected.")
        self._populate_entry_list()

        # ── Auto-save cache ────────────────────────────────────────────────
        if self._entries and not self._stop_event.is_set():
            self._save_entries_cache(path, self._entries)
            cache_path = self._cache_path_for(path)
            self._cache_status_var.set(f"✓ Cache saved: {cache_path.name}")
            self._load_cache_btn.configure(state="normal")

    def _plog(self, msg: str):
        self._parse_log.configure(state="normal")
        self._parse_log.insert("end", msg + "\n")
        self._parse_log.see("end")
        self._parse_log.configure(state="disabled")
        self.update_idletasks()

    # ── Pre-parsed section actions ────────────────────────────────────────

    def _browse_preparsed(self):
        path = filedialog.askopenfilename(
            title="Select pre-parsed diary JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self._preparsed_var.set(path)
            # Reset state when a new file is selected
            self._preparsed_messages: list[dict] = []
            self._pp_stage_btn.configure(state="disabled")
            self._pp_summary_var.set("")

    def _run_load_preparsed(self):
        path = self._preparsed_var.get().strip()
        if not path:
            messagebox.showerror("Error", "Please select a pre-parsed JSON file.")
            return
        threading.Thread(
            target=self._load_preparsed_worker,
            args=(path,),
            daemon=True,
        ).start()

    def _load_preparsed_worker(self, path: str):
        """Load raw Gemini-format messages, storing them for direct staging."""
        import json
        self._pplog(f"Loading: {Path(path).name}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError(
                    f"Expected a JSON array at the top level, got {type(data).__name__}.\n"
                    "The file must be a list of {{datetime_parsed, user, ...}} objects."
                )
            # Filter to entries that have a parseable date and user text
            valid = [
                item for item in data
                if isinstance(item, dict)
                and (item.get("datetime_parsed") or item.get("datetime_raw"))
                and item.get("user", "").strip()
            ]
            if not valid:
                raise ValueError(
                    f"No valid entries found in {len(data)} items. "
                    "Each item needs 'datetime_parsed' and 'user' fields."
                )
        except (ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("Load error", str(exc))
            self._pplog(f"✗ {exc}")
            return
        except Exception as exc:
            messagebox.showerror("Load error", f"Unexpected error:\n{exc}")
            self._pplog(f"✗ {exc}")
            return

        # Store raw messages for staging
        self._preparsed_messages = valid

        # Count user vs response entries
        n_user = sum(1 for m in valid if m.get("user", "").strip())
        n_resp = sum(1 for m in valid if m.get("response", "").strip())
        total_entries = n_user + n_resp

        summary = (
            f"✓ {len(valid)} messages loaded  "
            f"({n_user} user + {n_resp} assistant entries = {total_entries} assembler rows)"
        )
        self._pp_summary_var.set(summary)
        self._pplog(summary)
        self._pplog("Click '▶▶ Stage All Entries →' to add them to the assembly queue.")
        self._pp_stage_btn.configure(state="normal")
        self.app.set_status(f"Pre-parsed: {len(valid)} messages loaded, ready to stage.")

        # Also populate Review tab for inspection (user entries only)
        from ..parsers.preparsed_loader import load_preparsed_json
        try:
            self._entries = load_preparsed_json(path)
            self._populate_entry_list()
        except Exception:
            pass  # Review tab update is best-effort

    def _stage_preparsed(self):
        """Stage all entries from the loaded pre-parsed file (user + response)."""
        messages = getattr(self, "_preparsed_messages", [])
        if not messages:
            messagebox.showerror("Nothing loaded", "Load a pre-parsed file first.")
            return

        from datetime import datetime
        source_name = Path(self._preparsed_var.get()).name
        entries = []
        for msg in messages:
            dt_str = msg.get("datetime_parsed") or msg.get("datetime_raw", "")
            if not dt_str:
                continue
            # Handle both ISO and raw date strings
            try:
                dt = datetime.fromisoformat(dt_str)
            except ValueError:
                try:
                    # Fallback: parse just the date part
                    dt = datetime.strptime(dt_str[:10], "%Y-%m-%d")
                except ValueError:
                    continue

            date = dt.strftime("%Y-%m-%d")
            time = dt.strftime("%H:%M:%S")

            user_text = msg.get("user", "").strip()
            if user_text:
                entries.append({
                    "date": date, "time": time,
                    "source": "old_diary", "source_file": source_name,
                    "role": "user", "text": user_text,
                })
            response_text = msg.get("response", "").strip()
            if response_text:
                entries.append({
                    "date": date, "time": time,
                    "source": "old_diary", "source_file": source_name,
                    "role": "assistant", "text": response_text,
                })

        if not entries:
            messagebox.showwarning("Empty", "No stageable entries found in the loaded file.")
            return

        self.app.stage_entries(entries, f"Legacy Diary / Pre-parsed ({source_name})")
        self._pplog(f"✓ Staged {len(entries)} entries from {source_name}.")
        self._pp_stage_btn.configure(state="disabled")  # prevent double-staging
        self.app.set_status(f"Pre-parsed staged: {len(entries)} entries added to queue.")


    def _pplog(self, msg: str):
        self._preparsed_log.configure(state="normal")
        self._preparsed_log.insert("end", msg + "\n")
        self._preparsed_log.see("end")
        self._preparsed_log.configure(state="disabled")
        self.update_idletasks()

    # ── Review actions ────────────────────────────────────────────────────

    def _populate_entry_list(self):
        self._entry_listbox.delete(0, tk.END)
        for e in self._entries:
            dt = e.get("datetime_parsed", "NONE")
            date_disp = dt[:10] if dt else "NONE"
            self._entry_listbox.insert(tk.END, f"[{date_disp}]")
        self._entry_count_var.set(f"{len(self._entries)} entries")

    def _on_entry_select(self, _event=None):
        sel = self._entry_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        e = self._entries[idx]
        dt = e.get("datetime_parsed", "")
        self._date_var.set(dt[:10] if dt else "NONE")
        self._entry_text.configure(state="normal")
        self._entry_text.delete("1.0", "end")
        self._entry_text.insert("1.0", e.get("user", ""))
        self._entry_text.configure(state="disabled")

    def _apply_date(self):
        import re
        sel = self._entry_listbox.curselection()
        if not sel:
            messagebox.showwarning("Apply", "Select an entry first.")
            return
        idx = sel[0]
        new_date = self._date_var.get().strip().upper()
        if new_date == "NONE":
            self._entries[idx]["datetime_parsed"] = None
        else:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", new_date):
                messagebox.showerror("Format error", "Use YYYY-MM-DD or NONE")
                return
            self._entries[idx]["datetime_parsed"] = f"{new_date}T00:00:00"
        self._populate_entry_list()
        self._entry_listbox.selection_set(idx)

    def _stage(self):
        if not self._entries:
            messagebox.showerror("No entries", "Load entries first (Parse tab).")
            return
        from ..parsers.old_diary_parser import to_assembler_entries
        source = self._filepath or self._preparsed_var.get() or "old_diary.json"
        entries = to_assembler_entries(self._entries, source)
        if entries:
            self.app.stage_entries(entries, f"Legacy Diary ({Path(source).name})")
        else:
            messagebox.showwarning("Empty", "No dated entries to stage.")
