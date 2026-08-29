"""
tab_run.py
==========
Tab 2 â€” Run.

Features:
  - API Endpoint URL + optional API key (for OpenRouter / cloud providers)
  - Model dropdown (probed from /v1/models)
  - Concurrency slider (1 = serial, N = async parallel requests)
  - Prompt template editor
  - Context window selector (32k increments up to 256k)
  - Output max tokens (default 16k, range up to 32k)
  - Output directory picker
  - Start / Pause / Stop controls
  - Progress bar + live log
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .app import DARK


def _project_root() -> Path:
    """Return the project root (the inner diary_core git repo directory)."""
    # This file is at: <root>/diary_core/gui/tab_run.py
    return Path(__file__).resolve().parents[3]


class RunTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._pause_event = threading.Event()  # set = paused
        self._stop_event  = threading.Event()  # set = stop requested
        self._worker_thread: threading.Thread | None = None
        self._build()

    def _build(self):
        # â”€â”€ API Endpoint settings â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        srv_frame = ttk.LabelFrame(self, text="API Endpoint", padding=10)
        srv_frame.pack(fill="x", padx=14, pady=(14, 6))

        tk.Label(srv_frame, text="Base URL:", bg=DARK["bg"], fg=DARK["fg"]).grid(row=0, column=0, sticky="w")
        import os
        default_api_base = os.getenv("ANALYZER_API_BASE") or os.getenv("OPENROUTER_API_BASE") or os.getenv("LLM_API_BASE", "https://openrouter.ai/api")
        default_api_key = os.getenv("ANALYZER_API_KEY") or os.getenv("OPENROUTER_API_KEY") or os.getenv("LLM_API_KEY", "")
        default_model = os.getenv("ANALYZER_MODEL") or os.getenv("OPENROUTER_MODEL") or os.getenv("LLM_MODEL", "deepseek/deepseek-v4-pro")
        self.api_base_var = tk.StringVar(value=default_api_base)
        ttk.Entry(srv_frame, textvariable=self.api_base_var, width=44).grid(row=0, column=1, padx=8, sticky="w")
        ttk.Button(srv_frame, text="Probe models", command=self._probe_models).grid(row=0, column=2, padx=4)
        ttk.Button(srv_frame, text="Browse Models\u2026", command=self._browse_models).grid(row=0, column=3, padx=4)
        ttk.Button(srv_frame, text="Test connection", command=self._test_connection).grid(row=0, column=4, padx=4)

        tk.Label(
            srv_frame,
            text="\u26a0\ufe0f  Do NOT include /v1 in the Base URL \u2014 it is appended automatically."
                 "  OpenRouter: https://openrouter.ai/api  \u00b7  Local llama.cpp: http://localhost:8080",
            bg=DARK["bg"], fg=DARK["fg2"], font=("Helvetica", 8, "italic"),
            wraplength=680, justify="left",
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(2, 0))

        tk.Label(srv_frame, text="API Key:", bg=DARK["bg"], fg=DARK["fg"]).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.api_key_var = tk.StringVar(value=default_api_key)
        ttk.Entry(srv_frame, textvariable=self.api_key_var, width=44, show="*").grid(
            row=1, column=1, padx=8, sticky="w", pady=(6, 0))
        tk.Label(srv_frame, text="(leave blank for local servers)",
                 bg=DARK["bg"], fg=DARK["fg2"], font=("Helvetica", 8, "italic")
                 ).grid(row=1, column=2, columnspan=2, sticky="w", padx=4, pady=(6, 0))

        tk.Label(srv_frame, text="Model:", bg=DARK["bg"], fg=DARK["fg"]).grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.model_var = tk.StringVar(value=default_model)
        self.model_combo = ttk.Combobox(srv_frame, textvariable=self.model_var, width=42)
        self.model_combo.grid(row=3, column=1, padx=8, sticky="w", pady=(6, 0))

        # â”€â”€ Concurrency â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        conc_frame = ttk.LabelFrame(self, text="Concurrency", padding=10)
        conc_frame.pack(fill="x", padx=14, pady=(0, 6))

        self.concurrency_var = tk.IntVar(value=1)
        tk.Label(conc_frame, text="Parallel requests:", bg=DARK["bg"], fg=DARK["fg"],
                 font=("Helvetica", 9)).grid(row=0, column=0, sticky="w")
        tk.Scale(conc_frame, from_=1, to=20, orient="horizontal", variable=self.concurrency_var,
                 bg=DARK["bg"], fg=DARK["fg"], troughcolor=DARK["bg3"],
                 activebackground=DARK["accent"], highlightthickness=0,
                 length=220).grid(row=0, column=1, padx=10, sticky="w")
        tk.Label(conc_frame, textvariable=self.concurrency_var, bg=DARK["bg"], fg=DARK["accent"],
                 font=("Helvetica", 10, "bold"), width=3).grid(row=0, column=2, sticky="w")
        tk.Label(conc_frame,
                 text="1 = sequential (safe default) Â· 5-10 = recommended for OpenRouter Â· "
                      "days processed in parallel, weeks/months/year stay sequential",
                 bg=DARK["bg"], fg=DARK["fg2"], font=("Helvetica", 8, "italic"),
                 wraplength=560, justify="left",
                 ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))

        # â”€â”€ Context window & token budget â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        ctx_frame = ttk.LabelFrame(self, text="Context Window & Token Budget", padding=10)
        ctx_frame.pack(fill="x", padx=14, pady=(0, 6))

        tk.Label(ctx_frame, text="Context size:", bg=DARK["bg"], fg=DARK["fg"]).grid(row=0, column=0, sticky="w")
        self.ctx_var = tk.StringVar(value="32k")
        ttk.Combobox(
            ctx_frame, textvariable=self.ctx_var,
            values=["32k","64k","96k","128k","160k","192k","224k","256k"],
            width=8, state="readonly"
        ).grid(row=0, column=1, padx=8, sticky="w")

        tk.Label(ctx_frame, text="Output max tokens:", bg=DARK["bg"], fg=DARK["fg"],
                 ).grid(row=0, column=2, sticky="w", padx=(16, 0))
        self.max_tokens_var = tk.IntVar(value=16384)
        ttk.Spinbox(ctx_frame, from_=256, to=32768, increment=256,
                    textvariable=self.max_tokens_var, width=8).grid(row=0, column=3, padx=8, sticky="w")
        tk.Label(ctx_frame, text="(default 16384 â‰ˆ 16k; OpenRouter supports up to 32k+ on most models)",
                 bg=DARK["bg"], fg=DARK["fg2"], font=("Helvetica", 8, "italic")
                 ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        tk.Label(ctx_frame, text="Output format:", bg=DARK["bg"], fg=DARK["fg"]).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.fmt_var = tk.StringVar(value="section")
        ttk.Combobox(ctx_frame, textvariable=self.fmt_var, values=["section", "json"],
                     width=10, state="readonly").grid(row=2, column=1, padx=8, sticky="w", pady=(6, 0))
        tk.Label(ctx_frame, text="(section = robust default; json = opt-in for larger frontier models)",
                 bg=DARK["bg"], fg=DARK["fg2"], font=("Helvetica", 8)
                 ).grid(row=2, column=2, columnspan=3, sticky="w", padx=(8, 0), pady=(6, 0))

        # â”€â”€ Prompt template editor â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        tmpl_frame = ttk.LabelFrame(self, text="Prompt Template Editor", padding=10)
        tmpl_frame.pack(fill="x", padx=14, pady=(0, 6))

        tmpl_top = ttk.Frame(tmpl_frame)
        tmpl_top.pack(fill="x")
        tk.Label(tmpl_top, text="Template:", bg=DARK["bg"], fg=DARK["fg"]).pack(side="left")

        from diary_core.prompt_templates import TEMPLATES
        self.tmpl_key_var = tk.StringVar(value="day_isolated")
        self.tmpl_combo = ttk.Combobox(
            tmpl_top, textvariable=self.tmpl_key_var,
            values=list(TEMPLATES.keys()), width=24, state="readonly"
        )
        self.tmpl_combo.pack(side="left", padx=8)
        self.tmpl_combo.bind("<<ComboboxSelected>>", self._on_template_selected)

        ttk.Button(tmpl_top, text="Reset to default", command=self._reset_template).pack(side="left", padx=4)
        ttk.Button(tmpl_top, text="Load from fileâ€¦", command=self._load_template_file).pack(side="left", padx=4)

        self.tmpl_text = tk.Text(tmpl_frame, height=10, bg=DARK["bg2"], fg=DARK["fg"],
                                 font=("Courier", 9), relief="flat",
                                 insertbackground=DARK["fg"], wrap="word")
        self.tmpl_text.pack(fill="both", expand=True, pady=(6, 0))
        self._load_default_template()

        # â”€â”€ Output directory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        out_frame = ttk.LabelFrame(self, text="Output Directory", padding=10)
        out_frame.pack(fill="x", padx=14, pady=(0, 6))
        
        dir_top = ttk.Frame(out_frame)
        dir_top.pack(fill="x")
        self.output_dir_var = tk.StringVar(
            value=str(_project_root())
        )
        ttk.Entry(dir_top, textvariable=self.output_dir_var, width=56).pack(side="left", fill="x", expand=True)
        ttk.Button(dir_top, text="Browseâ€¦", command=self._browse_output).pack(side="left", padx=(8, 0))
        
        self.cascade_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(out_frame, text="Cascade updates (recalculate upper levels if new days are added)",
                        variable=self.cascade_var).pack(anchor="w", pady=(8, 0))

        # â”€â”€ Run controls â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        ctrl = ttk.Frame(self)
        ctrl.pack(fill="x", padx=14, pady=6)
        self.start_btn  = ttk.Button(ctrl, text="â–¶  Start", command=self._start)
        self.start_btn.pack(side="left", padx=(0, 6))
        self.pause_btn  = ttk.Button(ctrl, text="â¸  Pause", command=self._toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=6)
        self.stop_btn   = ttk.Button(ctrl, text="â¹  Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)

        # â”€â”€ Progress â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        prog_frame = ttk.Frame(self)
        prog_frame.pack(fill="x", padx=14, pady=(0, 6))
        self.progress = ttk.Progressbar(prog_frame, mode="determinate", length=600)
        self.progress.pack(side="left", fill="x", expand=True)
        self.prog_label_var = tk.StringVar(value="")
        tk.Label(prog_frame, textvariable=self.prog_label_var,
                 bg=DARK["bg"], fg=DARK["fg"], font=("Helvetica", 9), width=18, anchor="w"
                 ).pack(side="left", padx=8)

        # â”€â”€ Log â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        log_frame = ttk.LabelFrame(self, text="Log", padding=8)
        log_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.log_text = scrolledtext.ScrolledText(
            log_frame, wrap="word", bg=DARK["bg2"], fg=DARK["fg"],
            font=("Courier", 9), relief="flat", state="disabled",
            insertbackground=DARK["fg"],
        )
        self.log_text.pack(fill="both", expand=True)

    # â”€â”€ API / Server helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _get_client(self):
        from diary_core.llm_client import LLMClient
        return LLMClient(
            api_base=self.api_base_var.get().strip(),
            model_name=self.model_var.get().strip(),
            api_key=self.api_key_var.get().strip(),
        )

    def _probe_models(self):
        """
        Fetch the model list in a background thread so the GUI stays responsive.
        (Bug 5 fix: previously this ran synchronously on the GUI thread, causing a freeze.)
        """
        self.log("Probing models\u2026")

        def _worker():
            try:
                client = self._get_client()
                models = client.fetch_models()
                def _update():
                    self.model_combo["values"] = models
                    if models and self.model_var.get() not in models:
                        self.model_combo.current(0)
                    self.log(f"Found {len(models)} model(s).")
                self.after(0, _update)
            except Exception as exc:
                msg = str(exc)
                self.after(0, lambda: (
                    self.log(f"\u2717 Probe failed: {msg}"),
                    messagebox.showerror("Probe failed", msg),
                ))

        threading.Thread(target=_worker, daemon=True).start()

    def _browse_models(self):
        """Open the ModelBrowserDialog; selecting a model copies its ID to the combobox."""
        from diary_core.gui.model_browser import ModelBrowserDialog

        def _on_select(model_id: str):
            current_values = list(self.model_combo["values"] or [])
            if model_id not in current_values:
                current_values.insert(0, model_id)
                self.model_combo["values"] = current_values
            self.model_var.set(model_id)
            self.log(f"\u2713 Model set to: {model_id}")

        ModelBrowserDialog(self, client=self._get_client(), on_select=_on_select)

    def _test_connection(self):
        try:
            self._get_client().test_connection()
            self.log("âœ“ Server is reachable.")
            messagebox.showinfo("Connection OK", "Server is reachable.")
        except Exception as exc:
            self.log(f"âœ— Connection failed: {exc}")
            messagebox.showerror("Connection failed", str(exc))

    # â”€â”€ Template helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _load_default_template(self):
        from diary_core.prompt_templates import TEMPLATES
        key = self.tmpl_key_var.get()
        self.tmpl_text.delete("1.0", "end")
        self.tmpl_text.insert("1.0", TEMPLATES.get(key, ""))

    def _on_template_selected(self, _event=None):
        self._load_default_template()

    def _reset_template(self):
        self._load_default_template()

    def _load_template_file(self):
        path = filedialog.askopenfilename(
            title="Load prompt template", filetypes=[("Text files", "*.txt"), ("All", "*.*")]
        )
        if path:
            with open(path, "r", encoding="utf-8") as f:
                self.tmpl_text.delete("1.0", "end")
                self.tmpl_text.insert("1.0", f.read())

    # â”€â”€ Output â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _browse_output(self):
        d = filedialog.askdirectory(title="Select output directory")
        if d:
            self.output_dir_var.set(d)

    # â”€â”€ Run controls â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _start(self):
        cfg_tab = self.app.config_tab
        input_file = cfg_tab.input_var.get().strip()
        if not input_file:
            messagebox.showerror("Error", "Please select an input file in the Config tab.")
            return

        self._stop_event.clear()
        self._pause_event.clear()
        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal")
        self.stop_btn.config(state="normal")

        self._worker_thread = threading.Thread(target=self._run_worker, daemon=True)
        self._worker_thread.start()

    def _toggle_pause(self):
        if self._pause_event.is_set():
            self._pause_event.clear()
            self.pause_btn.config(text="â¸  Pause")
            self.log("â–¶ Resumed.")
        else:
            self._pause_event.set()
            self.pause_btn.config(text="â–¶  Resume")
            self.log("â¸ Paused (will stop after current unit completes).")

    def _stop(self):
        self._stop_event.set()
        self._pause_event.clear()
        self.log("â¹ Stop signal sent â€” waiting for current unit to finishâ€¦")

    def _on_run_complete(self):
        self.start_btn.config(state="normal")
        self.pause_btn.config(state="disabled", text="â¸  Pause")
        self.stop_btn.config(state="disabled")

    # â”€â”€ Worker â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _run_worker(self):
        try:
            self._do_run()
        except Exception as exc:
            self.log(f"\nâœ— Unexpected error: {exc}")
            import traceback
            self.log(traceback.format_exc())
        finally:
            self.after(0, self._on_run_complete)

    def _do_run(self):
        from diary_core.input_loader import load_merged_json
        from diary_core.llm_client import LLMClient
        from diary_core.progress_manager import ProgressManager
        from diary_core.summarizer import SummarizationConfig
        from diary_core.hierarchy_builder import HierarchyBuilder
        from diary_core.token_estimator import context_window_from_label

        cfg_tab = self.app.config_tab

        # Load input data
        self.log("Loading diary dataâ€¦")
        days = load_merged_json(cfg_tab.input_var.get().strip())
        target_range = cfg_tab.get_date_range()

        self.log(f"  Loaded {len(days)} days of entries into memory pool.")
        if target_range:
            self.log(f"  Target summary range: {target_range[0]} to {target_range[1]}")

        if not days:
            self.log("âœ— No entries found in the selection.")
            return

        mode = cfg_tab.get_mode()
        concurrency = self.concurrency_var.get()

        # Custom template (if the user edited it)
        tmpl_key = self.tmpl_key_var.get()
        custom_tmpl_text = self.tmpl_text.get("1.0", "end").strip()
        custom_templates = {tmpl_key: custom_tmpl_text} if custom_tmpl_text else {}

        ctx_tokens = context_window_from_label(self.ctx_var.get())
        run_cfg = SummarizationConfig(
            mode=mode,
            history_n=cfg_tab.history_n_var.get(),
            context_window=ctx_tokens,
            output_budget=self.max_tokens_var.get(),
            max_tokens=self.max_tokens_var.get(),
            temperature=0.3,
            output_format=self.fmt_var.get(),
            custom_templates=custom_templates,
            concurrency=concurrency,
            api_key=self.api_key_var.get().strip(),
        )

        client = LLMClient(
            api_base=self.api_base_var.get().strip(),
            model_name=self.model_var.get().strip(),
            api_key=self.api_key_var.get().strip(),
        )
        progress = ProgressManager(
            output_dir=self.output_dir_var.get().strip(),
            run_config={"mode": mode},
        )

        done = progress.status_summary()
        if done:
            self.log(f"  Resuming: already completed â†’ {done}")

        if concurrency > 1:
            self.log(f"  Concurrency: {concurrency} parallel requests (async)")
        else:
            self.log("  Concurrency: 1 (sequential)")

        builder = HierarchyBuilder(
            client=client,
            progress=progress,
            config=run_cfg,
            log=self.log,
            pause_event=self._pause_event,
            stop_event=self._stop_event,
        )

        levels = cfg_tab.get_levels()

        target_days = [d for d in days if target_range[0] <= d.date <= target_range[1]] if target_range else days
        total_days = len(target_days)
        self.progress["maximum"] = total_days
        self.progress["value"] = 0

        results = builder.run(
            days=days,
            levels=levels,
            target_range=target_range,
            cascade_updates=self.cascade_var.get(),
        )

        day_results = results.get("day", [])
        if target_range:
            saved_count = sum(1 for s in day_results if target_range[0] <= s.period_start <= target_range[1])
        else:
            saved_count = len(day_results)

        self.progress["value"] = saved_count
        self.prog_label_var.set(f"{saved_count} / {total_days} days")

        self.log(f"\nâœ“ Run complete. Results by level:")
        for lvl, items in results.items():
            if isinstance(items, list):
                self.log(f"  {lvl}: {len(items)} units")
        self.app.set_status("Run complete. Switch to Browse tab to explore results.")
        self.after(0, lambda: self.app.browse_tab.load_from_directory(
            self.output_dir_var.get().strip()
        ))

    # â”€â”€ Log helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def log(self, msg: str):
        def _append():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(0, _append)

    def set_progress(self, value: int, maximum: int):
        def _update():
            self.progress["maximum"] = maximum
            self.progress["value"] = value
            self.prog_label_var.set(f"{value} / {maximum}")
        self.after(0, _update)
