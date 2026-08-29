"""
tab_gemini.py
=============
Tab 2 — Gemini Activity Parser + Classifier

Sub-tabs:
  Step 1 — Parse HTML
  Step 2 — Classify with checkpoint resume
  Step 3 — Stage to Assembler
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
    # This file is at: <root>/diary_ingestor/gui/tab_gemini.py
    return Path(__file__).resolve().parents[3]


def _cache_dir() -> Path:
    return _project_root() / "data" / "raw_data" / "cache"


DEFAULT_CATEGORIES = [
    "self_reflection_psychology_therapy_dating",
    "other_personal",
    "coding_related",
    "other",
]
DIARY_DEFAULTS = {"self_reflection_psychology_therapy_dating", "other_personal"}

# Categories that should be ticked by default when Step 3 is first populated
_DIARY_DEFAULT_CHECKED = {"self_reflection_psychology_therapy_dating", "other_personal"}


class GeminiTab(ttk.Frame):
    def __init__(self, parent, app: "IngestorApp"):
        super().__init__(parent)
        self.app = app
        self._messages: list[dict] = []
        self._checkpoint: dict[int, str] = {}
        self._source_file: str = ""
        self._stop_event = threading.Event()
        self._build()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        self._step1 = ttk.Frame(nb)
        self._step2 = ttk.Frame(nb)
        self._step3 = ttk.Frame(nb)
        nb.add(self._step1, text="  Step 1 — Parse HTML  ")
        nb.add(self._step2, text="  Step 2 — Classify  ")
        nb.add(self._step3, text="  Step 3 — Stage  ")

        self._build_step1(self._step1)
        self._build_step2(self._step2)
        self._build_step3(self._step3)

    # ── Step 1 ────────────────────────────────────────────────────────────

    def _build_step1(self, parent):
        # Input file
        f1 = ttk.LabelFrame(parent, text="MyActivity.html File", padding=8)
        f1.pack(fill="x", padx=12, pady=(12, 6))
        self._html_var = tk.StringVar()
        ttk.Entry(f1, textvariable=self._html_var, width=60).pack(side="left", fill="x", expand=True)
        ttk.Button(f1, text="Browse…", command=self._browse_html).pack(side="left", padx=6)

        # Status line
        f2 = ttk.LabelFrame(parent, text="Classification Checkpoint", padding=8)
        f2.pack(fill="x", padx=12, pady=(0, 6))
        self._cache_path_var = tk.StringVar(value="(browse a file to see checkpoint path)")
        tk.Label(f2, textvariable=self._cache_path_var, bg=DARK["bg"], fg=DARK["cyan"],
                 font=("Courier", 9), anchor="w").pack(fill="x")

        # Controls
        ctrl = ttk.Frame(parent)
        ctrl.pack(fill="x", padx=12, pady=4)
        ttk.Button(ctrl, text="▶ Parse HTML", command=self._run_parse).pack(side="left")
        self._parse_count_var = tk.StringVar(value="")
        tk.Label(ctrl, textvariable=self._parse_count_var, bg=DARK["bg"], fg=DARK["green"],
                 font=("Helvetica", 10)).pack(side="left", padx=10)

        # Preview
        pf = ttk.LabelFrame(parent, text="Preview (first 5 messages)", padding=8)
        pf.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self._preview = scrolledtext.ScrolledText(
            pf, wrap="word", bg=DARK["bg2"], fg=DARK["fg"],
            font=("Courier", 9), relief="flat", state="disabled",
        )
        self._preview.pack(fill="both", expand=True)

    # ── Step 2 ────────────────────────────────────────────────────────────

    def _build_step2(self, parent):
        # LLM config
        lf = ttk.LabelFrame(parent, text="LLM Configuration", padding=8)
        lf.pack(fill="x", padx=12, pady=(12, 6))

        # Backend selector
        self._backend_var = tk.StringVar(value="local")
        ttk.Radiobutton(lf, text="Local (llama.cpp)", variable=self._backend_var,
                        value="local").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(lf, text="OpenRouter", variable=self._backend_var,
                        value="openrouter").grid(row=0, column=1, sticky="w", padx=20)

        import os
        default_api_base = os.getenv("INGESTOR_API_BASE") or os.getenv("LLM_API_BASE", "http://localhost:8080")
        default_model = os.getenv("INGESTOR_MODEL") or os.getenv("LLM_MODEL", "local-model")
        default_api_key = os.getenv("INGESTOR_API_KEY") or os.getenv("OPENROUTER_API_KEY", "")

        tk.Label(lf, text="API Base / URL:", bg=DARK["bg"], fg=DARK["fg"]).grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        self._api_base_var = tk.StringVar(value=default_api_base)
        ttk.Entry(lf, textvariable=self._api_base_var, width=44).grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))

        tk.Label(lf, text="Model name:", bg=DARK["bg"], fg=DARK["fg"]).grid(row=2, column=0, sticky="w")
        self._model_var = tk.StringVar(value=default_model)
        ttk.Entry(lf, textvariable=self._model_var, width=44).grid(row=2, column=1, columnspan=2, sticky="w")

        tk.Label(lf, text="API Key (OpenRouter):", bg=DARK["bg"], fg=DARK["fg"]).grid(row=3, column=0, sticky="w")
        self._apikey_var = tk.StringVar(value=default_api_key)
        ttk.Entry(lf, textvariable=self._apikey_var, width=44, show="*").grid(row=3, column=1, columnspan=2, sticky="w")

        ttk.Button(lf, text="Test Connection", command=self._test_conn).grid(row=1, column=3, padx=10, rowspan=2, sticky="ns")

        # Categories
        cf = ttk.LabelFrame(parent, text="Categories (one per line)", padding=8)
        cf.pack(fill="x", padx=12, pady=(0, 6))
        self._cat_text = tk.Text(
            cf, height=5, bg=DARK["bg2"], fg=DARK["fg"],
            font=("Courier", 9), relief="flat", insertbackground=DARK["fg"],
        )
        self._cat_text.pack(fill="x")
        self._cat_text.insert("1.0", "\n".join(DEFAULT_CATEGORIES))

        # Checkpoint controls
        cpf = ttk.LabelFrame(parent, text="Checkpoint / Save-state", padding=8)
        cpf.pack(fill="x", padx=12, pady=(0, 6))
        self._force_reclassify = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            cpf,
            text="Force full reclassify (ignores saved checkpoint — use when you change the prompt or categories)",
            variable=self._force_reclassify,
        ).pack(anchor="w")
        self._checkpoint_status_var = tk.StringVar(value="No checkpoint loaded")
        tk.Label(cpf, textvariable=self._checkpoint_status_var, bg=DARK["bg"],
                 fg=DARK["subtext"], font=("Helvetica", 9)).pack(anchor="w")

        # Progress + controls
        pf = ttk.Frame(parent)
        pf.pack(fill="x", padx=12, pady=4)
        self._progress = ttk.Progressbar(pf, mode="determinate", length=500)
        self._progress.pack(side="left", fill="x", expand=True)
        self._prog_label = tk.Label(pf, text="", bg=DARK["bg"], fg=DARK["fg"],
                                    font=("Helvetica", 9), width=14, anchor="w")
        self._prog_label.pack(side="left", padx=6)

        btn_row = ttk.Frame(parent)
        btn_row.pack(fill="x", padx=12, pady=4)
        ttk.Button(btn_row, text="▶ Classify", command=self._run_classify).pack(side="left")
        ttk.Button(btn_row, text="⏹ Stop", command=self._stop_classify).pack(side="left", padx=6)

        # Log
        lf2 = ttk.LabelFrame(parent, text="Log", padding=8)
        lf2.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self._classify_log = scrolledtext.ScrolledText(
            lf2, wrap="word", bg=DARK["bg2"], fg=DARK["fg"],
            font=("Courier", 9), relief="flat", state="disabled",
        )
        self._classify_log.pack(fill="both", expand=True)

    # ── Step 3 ────────────────────────────────────────────────────────────

    def _build_step3(self, parent):
        # ── Category checkboxes ───────────────────────────────────────────
        df = ttk.LabelFrame(parent, text="Diary Categories to Include", padding=8)
        df.pack(fill="x", padx=12, pady=(12, 6))

        tk.Label(
            df,
            text="Tick the categories you want to stage. Run Step 2 first — the list "
                 "is populated automatically from the classification results.",
            bg=DARK["bg"], fg=DARK["subtext"], font=("Helvetica", 9), justify="left",
        ).pack(anchor="w", pady=(0, 4))

        # Scrollable inner frame for the checkboxes
        cb_outer = tk.Frame(df, bg=DARK["bg2"], relief="flat", bd=1)
        cb_outer.pack(fill="x")
        self._cb_canvas = tk.Canvas(
            cb_outer, bg=DARK["bg2"], highlightthickness=0, height=100
        )
        cb_scroll = ttk.Scrollbar(cb_outer, orient="vertical", command=self._cb_canvas.yview)
        self._cb_frame = tk.Frame(self._cb_canvas, bg=DARK["bg2"])
        self._cb_frame.bind(
            "<Configure>",
            lambda e: self._cb_canvas.configure(scrollregion=self._cb_canvas.bbox("all")),
        )
        self._cb_canvas.create_window((0, 0), window=self._cb_frame, anchor="nw")
        self._cb_canvas.configure(yscrollcommand=cb_scroll.set)
        self._cb_canvas.pack(side="left", fill="both", expand=True)
        cb_scroll.pack(side="right", fill="y")

        # Dict of {category_name: BooleanVar} — populated by _refresh_step3_checkboxes
        self._cat_vars: dict[str, tk.BooleanVar] = {}

        btn_row = tk.Frame(df, bg=DARK["bg"])
        btn_row.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_row, text="Select All",
                   command=self._cb_select_all).pack(side="left", padx=(0, 4))
        ttk.Button(btn_row, text="Deselect All",
                   command=self._cb_deselect_all).pack(side="left")

        # ── Classification summary ────────────────────────────────────────
        sf = ttk.LabelFrame(parent, text="Classification Summary", padding=8)
        sf.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self._summary_text = scrolledtext.ScrolledText(
            sf, wrap="word", bg=DARK["bg2"], fg=DARK["fg"],
            font=("Courier", 9), relief="flat", state="disabled",
        )
        self._summary_text.pack(fill="both", expand=True)

        ttk.Button(parent, text="▶ Stage Selected Categories", command=self._stage).pack(
            anchor="w", padx=12, pady=4)

    def _cb_select_all(self):
        for var in self._cat_vars.values():
            var.set(True)

    def _cb_deselect_all(self):
        for var in self._cat_vars.values():
            var.set(False)

    def _refresh_step3_checkboxes(self, categories_in_results: list[str]):
        """Rebuild the checkbox list from the categories present in the current checkpoint."""
        # Destroy old checkboxes
        for widget in self._cb_frame.winfo_children():
            widget.destroy()
        self._cat_vars.clear()

        for cat in sorted(categories_in_results):
            var = tk.BooleanVar(value=(cat in _DIARY_DEFAULT_CHECKED))
            self._cat_vars[cat] = var
            ttk.Checkbutton(
                self._cb_frame,
                text=cat,
                variable=var,
            ).pack(anchor="w", pady=1)

        # Resize canvas to fit content
        self._cb_frame.update_idletasks()
        needed = self._cb_frame.winfo_reqheight()
        self._cb_canvas.configure(height=min(needed, 120))

    # ── Actions ───────────────────────────────────────────────────────────

    def _browse_html(self):
        path = filedialog.askopenfilename(
            title="Select MyActivity.html",
            initialdir=str(_project_root() / "data" / "raw_data"),
            filetypes=[("HTML files", "*.html"), ("All files", "*.*")],
        )
        if path:
            self._html_var.set(path)
            cache = _cache_dir() / (Path(path).stem + "_classify_checkpoint.json")
            self._cache_path_var.set(str(cache))

    def _run_parse(self):
        path = self._html_var.get().strip()
        if not path:
            messagebox.showerror("Error", "Please select an HTML file.")
            return
        threading.Thread(target=self._parse_worker, args=(path,), daemon=True).start()

    def _parse_worker(self, path: str):
        from ..parsers.gemini_parser import parse_gemini_html
        self.app.set_status("Parsing Gemini HTML…")
        try:
            self._messages = parse_gemini_html(path)
            self._source_file = path
            self._after_parse()
        except Exception as exc:
            messagebox.showerror("Parse error", str(exc))
            self.app.set_status("Parse failed.")

    def _after_parse(self):
        n = len(self._messages)
        self._parse_count_var.set(f"✓ {n} messages parsed")
        self.app.set_status(f"Gemini: {n} messages loaded.")

        # Load checkpoint status
        from ..progress_store import ProgressStore
        store = ProgressStore(str(_cache_dir()))
        cp = store.load_classify_checkpoint(self._source_file)
        self._checkpoint = cp
        if cp:
            self._checkpoint_status_var.set(f"Checkpoint found: {len(cp)}/{n} already classified")
        else:
            self._checkpoint_status_var.set("No checkpoint — will classify from scratch")

        # Preview
        self._preview.configure(state="normal")
        self._preview.delete("1.0", "end")
        for i, msg in enumerate(self._messages[:5]):
            self._preview.insert("end",
                f"{'─'*60}\n[{i+1}] {msg.get('datetime_raw','')}\n"
                f"USER:     {msg.get('user','')[:200]}\n"
                f"RESPONSE: {msg.get('response','')[:200]}\n\n")
        self._preview.configure(state="disabled")

    def _test_conn(self):
        from ..llm_client import LLMClient
        c = LLMClient(
            api_base=self._api_base_var.get().strip(),
            model_name=self._model_var.get().strip(),
            api_key=self._apikey_var.get().strip() or None,
        )
        try:
            result = c.test_connection()
            messagebox.showinfo("Connection OK", result)
        except Exception as exc:
            messagebox.showerror("Connection failed", str(exc))

    def _run_classify(self):
        if not self._messages:
            messagebox.showerror("No data", "Run Step 1 first to load messages.")
            return
        self._stop_event.clear()
        threading.Thread(target=self._classify_worker, daemon=True).start()

    def _stop_classify(self):
        self._stop_event.set()

    def _classify_worker(self):
        from ..llm_client import LLMClient
        from ..parsers.gemini_parser import classify_messages
        from ..progress_store import ProgressStore

        cats_raw = self._cat_text.get("1.0", "end").strip()
        categories = [c.strip() for c in cats_raw.splitlines() if c.strip()]
        if not categories:
            messagebox.showerror("Error", "Enter at least one category.")
            return

        store = ProgressStore(str(_cache_dir()))

        # Handle force-reclassify
        if self._force_reclassify.get():
            store.clear_classify_checkpoint(self._source_file)
            self._checkpoint = {}
            self._clog("Force reclassify: cleared existing checkpoint.")
        else:
            self._checkpoint = store.load_classify_checkpoint(self._source_file)
            already = len(self._checkpoint)
            if already:
                self._clog(f"Resuming from checkpoint: {already}/{len(self._messages)} already done.")

        client = LLMClient(
            api_base=self._api_base_var.get().strip(),
            model_name=self._model_var.get().strip(),
            api_key=self._apikey_var.get().strip() or None,
        )

        total = len(self._messages)

        def on_progress(cur, tot, cat):
            self._progress["maximum"] = tot
            self._progress["value"] = cur
            self._prog_label.config(text=f"{cur} / {tot}")
            self.update_idletasks()

        def on_checkpoint(cp: dict):
            store.save_classify_checkpoint(self._source_file, cp)
            self._checkpoint = cp

        self.app.set_status("Classifying…")
        self._clog(f"Classifying {total} messages with {len(categories)} categories…")

        result = classify_messages(
            self._messages, categories, client,
            checkpoint=self._checkpoint,
            on_progress=on_progress,
            on_checkpoint=on_checkpoint,
            stop_event=self._stop_event,
        )
        self._checkpoint = result

        # Print summary
        from collections import Counter
        counts = Counter(result.values())
        self._clog("\n── Classification Summary ──")
        for cat, n in sorted(counts.items()):
            self._clog(f"  {cat:40s} {n:>5}")
        self._clog(f"\n✓ Done — {len(result)}/{total} messages classified")
        self.app.set_status(f"Classification complete — {len(result)}/{total}")
        self._refresh_summary()

    def _clog(self, msg: str):
        self._classify_log.configure(state="normal")
        self._classify_log.insert("end", msg + "\n")
        self._classify_log.see("end")
        self._classify_log.configure(state="disabled")
        self.update_idletasks()

    def _refresh_summary(self):
        from collections import Counter
        self._summary_text.configure(state="normal")
        self._summary_text.delete("1.0", "end")
        counts = Counter(self._checkpoint.values())
        for cat, n in sorted(counts.items()):
            self._summary_text.insert("end", f"  {cat:40s} {n:>5}\n")
        self._summary_text.configure(state="disabled")
        # Rebuild Step 3 checkboxes to reflect the actual classified categories
        self._refresh_step3_checkboxes(list(counts.keys()))

    def _stage(self):
        if not self._messages or not self._checkpoint:
            messagebox.showerror("No data", "Classify messages first (Step 2).")
            return

        # Read category selection from checkboxes
        diary_cats = {cat for cat, var in self._cat_vars.items() if var.get()}
        if not diary_cats:
            messagebox.showwarning(
                "No categories selected",
                "Tick at least one category in Step 3 before staging.",
            )
            return

        from ..parsers.gemini_parser import to_assembler_entries
        entries = to_assembler_entries(
            self._messages, self._checkpoint,
            source_file=self._source_file,
            diary_categories=diary_cats,
        )
        if entries:
            self.app.stage_entries(entries, f"Gemini ({Path(self._source_file).name})")
        else:
            messagebox.showwarning("Empty", "No entries matched the selected diary categories.")
