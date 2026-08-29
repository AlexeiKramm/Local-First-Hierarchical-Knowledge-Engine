"""
tab_entity.py
=============
Entity Profiles tab â€” delegates to entity_tracker.run_pass1 / run_pass2.

Step 1: Scan existing day summaries to discover entities (people/themes).
Step 2: For each discovered entity, build an enriched timeline and generate
        a deep arc profile using the LLM. Arc synthesis is automatically
        chunked when the timeline exceeds the model's context window.

LLM settings (API URL + key) are inherited from the Run tab via shared app reference.
"""

from __future__ import annotations

import os
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .app import DARK


class EntityTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._current_entities: list[dict] = []
        self._build()

    def _build(self):
        canvas = tk.Canvas(self, bg=DARK["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        sf = ttk.Frame(canvas)
        sf.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=sf, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        tk.Label(sf,
                 text="Entity Profiling Pipeline: Automatically detect key people and themes from your daily summaries, then generate rich, timeline-based dossiers leveraging both summary and raw diary text.",
                 bg=DARK["bg"], fg=DARK["fg2"], font=("Helvetica", 9, "italic"),
                 wraplength=700, justify="left"
                 ).pack(anchor="w", padx=14, pady=(10, 4))

        # â”€â”€ Directory settings â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        dirs_frame = ttk.LabelFrame(sf, text="Define Input Sources & Output Destinations", padding=10)
        dirs_frame.pack(fill="x", padx=14, pady=(0, 6))

        # Row 0: main output dir (root of diary analyzer output)
        tk.Label(dirs_frame, text="Diary Analyzer Output Folder:",
                 bg=DARK["bg"], fg=DARK["fg"], font=("Helvetica", 9, "bold")
                 ).grid(row=0, column=0, sticky="w")
        self.output_dir_var = tk.StringVar()
        ttk.Entry(dirs_frame, textvariable=self.output_dir_var, width=52).grid(row=0, column=1, padx=8, sticky="ew")
        ttk.Button(dirs_frame, text="Browseâ€¦", command=lambda: self._browse_dir(self.output_dir_var)).grid(row=0, column=2)
        tk.Label(dirs_frame,
                 text="  â†³ The project root folder (contains data/, config/, logs/)",
                 bg=DARK["bg"], fg=DARK["fg2"], font=("Helvetica", 8, "italic")
                 ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 4))



        # â”€â”€ Enrichment / chunk settings â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        pass_frame = ttk.LabelFrame(sf, text="Configure Context Enrichment Settings", padding=10)
        pass_frame.pack(fill="x", padx=14, pady=(0, 6))

        tk.Label(pass_frame, text="Include context (days before mention):", bg=DARK["bg"], fg=DARK["fg"]).grid(row=0, column=0, sticky="w")
        self.days_before_var = tk.IntVar(value=3)
        ttk.Spinbox(pass_frame, from_=0, to=30, textvariable=self.days_before_var, width=5).grid(row=0, column=1, padx=8, sticky="w")

        tk.Label(pass_frame, text="Include context (days after mention):", bg=DARK["bg"], fg=DARK["fg"]).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.days_after_var = tk.IntVar(value=3)
        ttk.Spinbox(pass_frame, from_=0, to=30, textvariable=self.days_after_var, width=5).grid(row=1, column=1, padx=8, sticky="w", pady=(6, 0))

        tk.Label(pass_frame, text="LLM context window (tokens):", bg=DARK["bg"], fg=DARK["fg"]).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.context_window_var = tk.IntVar(value=32_768)
        ttk.Spinbox(pass_frame, from_=4096, to=1_048_576, increment=4096, width=10,
                    textvariable=self.context_window_var).grid(row=2, column=1, padx=8, sticky="w", pady=(6, 0))
        tk.Label(pass_frame, text="tokens â€” controls auto-chunking",
                 bg=DARK["bg"], fg=DARK["fg2"], font=("Helvetica", 8, "italic")
                 ).grid(row=2, column=2, sticky="w", pady=(6, 0))

        tk.Label(pass_frame, text="Chunk overlap (entries):", bg=DARK["bg"], fg=DARK["fg"]).grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.chunk_overlap_var = tk.IntVar(value=3)
        ttk.Spinbox(pass_frame, from_=0, to=20, width=5,
                    textvariable=self.chunk_overlap_var).grid(row=3, column=1, padx=8, sticky="w", pady=(6, 0))
        tk.Label(pass_frame, text="entries shared between consecutive chunks",
                 bg=DARK["bg"], fg=DARK["fg2"], font=("Helvetica", 8, "italic")
                 ).grid(row=3, column=2, sticky="w", pady=(6, 0))

        tk.Label(pass_frame,
                 text="â“˜ Note: LLM configuration (API Key, Model, and Endpoint) is actively inherited from the 'Run' tab.",
                 bg=DARK["bg"], fg=DARK["fg2"], font=("Helvetica", 8, "italic")
                 ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # â”€â”€ Controls â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        ctrl = ttk.Frame(sf)
        ctrl.pack(fill="x", padx=14, pady=6)

        self.pass1_btn = ttk.Button(ctrl, text="Step 1: Extract Mentioned Entities", command=self._run_pass1)
        self.pass1_btn.pack(side="left", padx=(0, 8))

        self.pass2_btn = ttk.Button(ctrl, text="Step 2: Generate Contextual Profiles", command=self._run_pass2)
        self.pass2_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = ttk.Button(ctrl, text="â¹ Stop Generation", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left")

        # â”€â”€ Discovered entities display â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        ent_frame = ttk.LabelFrame(sf, text="Entity Discovery Index", padding=8)
        ent_frame.pack(fill="x", padx=14, pady=(0, 6))

        ent_top = ttk.Frame(ent_frame)
        ent_top.pack(fill="x", pady=(0, 4))
        tk.Label(ent_top, text="Select entities to generate profiles for (click to toggle):", bg=DARK["bg"], fg=DARK["fg"]).pack(side="left")
        ttk.Button(ent_top, text="Load existing indexâ€¦", command=self._load_existing_index).pack(side="right")

        list_frame = ttk.Frame(ent_frame)
        list_frame.pack(fill="x")
        
        self.entity_list = tk.Listbox(
            list_frame, height=8, bg=DARK["bg2"], fg=DARK["fg"],
            font=("Courier", 9), relief="flat", selectbackground=DARK["accent"],
            selectforeground=DARK["bg"], selectmode=tk.MULTIPLE, exportselection=False
        )
        self.entity_list.pack(side="left", fill="x", expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.entity_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.entity_list.config(yscrollcommand=scrollbar.set)

        ent_bot = ttk.Frame(ent_frame)
        ent_bot.pack(fill="x", pady=(4, 0))
        ttk.Button(ent_bot, text="Select All", command=self._select_all_entities).pack(side="left", padx=(0, 4))
        ttk.Button(ent_bot, text="Select None", command=self._select_no_entities).pack(side="left")

        # â”€â”€ Log â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        log_frame = ttk.LabelFrame(sf, text="Log", padding=8)
        log_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.log_text = scrolledtext.ScrolledText(
            log_frame, wrap="word", bg=DARK["bg2"], fg=DARK["fg"],
            font=("Courier", 9), relief="flat", state="disabled",
            insertbackground=DARK["fg"],
        )
        self.log_text.pack(fill="both", expand=True)

    # â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _browse_dir(self, var: tk.StringVar):
        d = filedialog.askdirectory()
        if d:
            var.set(d)

    def _log(self, msg: str):
        def _append():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(0, _append)

    def _stop(self):
        self._stop_event.set()
        self._log("â¹ Stop requested.")

    def _set_buttons(self, running: bool):
        state_main = "disabled" if running else "normal"
        self.pass1_btn.config(state=state_main)
        self.pass2_btn.config(state=state_main)
        self.stop_btn.config(state="normal" if running else "disabled")

    # â”€â”€ Step 1: Entity Discovery â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _run_pass1(self):
        output_dir = self.output_dir_var.get().strip()
        if not output_dir or not os.path.isdir(output_dir):
            messagebox.showerror(
                "Error",
                "Please select the Diary Analyzer Output Folder first.\n"
                "This is the project root folder."
            )
            return
        self._stop_event.clear()
        self._set_buttons(True)
        self._worker_thread = threading.Thread(target=self._pass1_worker, daemon=True)
        self._worker_thread.start()

    def _pass1_worker(self):
        from diary_core.entity_tracker import run_pass1
        from mcp_server.db import get_db_connection, list_summaries
        output_dir = self.output_dir_var.get().strip()

        self._log("â•â•â• STEP 1 â€” Entity Discovery â•â•â•")

        db_path = Path(output_dir) / "diary.db"
        try:
            conn = get_db_connection(str(db_path))
        except Exception:
            self._log(f"âœ— Could not connect to database at: {db_path}")
            self._log("  Make sure the Diary Analyzer Output Folder contains data/diary.db.")
            self.after(0, lambda: self._set_buttons(False))
            return

        try:
            day_summaries = list_summaries(conn, "day")
            self._log(f"  Found {len(day_summaries)} day summaries in database.")

            index_path = run_pass1(db_conn=conn, output_dir=output_dir, log=self._log)
            self._log(f"\nâœ“ Entity index written â†’ {index_path}")
            import json
            with open(index_path, "r", encoding="utf-8") as f:
                index = json.load(f)
            self.after(0, lambda idx=index: self._populate_entity_list(idx))
        except Exception:
            import traceback
            self._log(f"\nâœ— Error:\n{traceback.format_exc()}")
        finally:
            conn.close()
            self.after(0, lambda: self._set_buttons(False))

    def _populate_entity_list(self, index: dict):
        """
        Populate the entity listbox from alias_map values ONLY.

        Shows one row per unique canonical ID (value in alias_map), sorted by
        number of aliases descending. The entities list is ignored â€” alias_map
        is the ground truth after manual merging.

        _current_entities stores {"id": canonical_id, "n_aliases": N} dicts so
        that Pass 2 receives the correct target_ids list.
        """
        from collections import defaultdict

        alias_map: dict[str, str] = index.get("alias_map", {})

        if not alias_map:
            self._log("  âš  alias_map is empty â€” run Pass 1 first.")
            return

        # Build reverse map: canonical_id â†’ list of all aliases pointing to it
        reverse_alias: dict[str, list[str]] = defaultdict(list)
        for alias, cid in alias_map.items():
            reverse_alias[cid].append(alias)

        # One row per unique canonical ID, sorted by alias count (most-merged first)
        rows: list[dict] = sorted(
            [
                {"id": cid, "aliases": sorted(als), "n_aliases": len(als)}
                for cid, als in reverse_alias.items()
            ],
            key=lambda r: -r["n_aliases"],
        )

        self._current_entities = rows
        self.entity_list.delete(0, "end")
        for r in rows:
            cid    = r["id"]
            n_al   = r["n_aliases"]
            # Show up to 4 aliases inline, then "â€¦+N more"
            shown  = r["aliases"][:4]
            extra  = n_al - len(shown)
            alias_preview = ", ".join(shown) + (f"  â€¦+{extra} more" if extra else "")
            self.entity_list.insert(
                "end",
                f"  {cid:<32}  [{n_al} alias(es): {alias_preview}]",
            )

        n_unique = len(rows)
        n_surface = len(alias_map)
        try:
            self._log(
                f"  Listed {n_unique} canonical entity ID(s) "
                f"covering {n_surface} surface-form aliases."
            )
        except Exception:
            pass



    def _select_all_entities(self):
        self.entity_list.selection_set(0, tk.END)

    def _select_no_entities(self):
        self.entity_list.selection_clear(0, tk.END)

    def _load_existing_index(self):
        import json
        output_dir = self.output_dir_var.get().strip()
        index_path = Path(output_dir) / "data" / "entity_index.json"
        if not index_path.exists():
            messagebox.showerror("Error", f"No entity_index.json found at:\n{index_path}")
            return
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                index = json.load(f)
            self._populate_entity_list(index)
            self._log(f"Loaded index â†’ {len(self._current_entities)} unique canonical entities.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load index:\n{e}")

    # â”€â”€ Step 2: Profile Generation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _run_pass2(self):
        output_dir = self.output_dir_var.get().strip()
        if not output_dir or not os.path.isdir(output_dir):
            messagebox.showerror("Error", "Please run Step 1 first (or select a valid output dir containing data/entity_index.json).")
            return
            
        selected_indices = self.entity_list.curselection()
        if not selected_indices:
            messagebox.showerror("Error", "No entities selected. Please select at least one entity from the list.")
            return

        selected_ids = [self._current_entities[i]["id"] for i in selected_indices]

        self._stop_event.clear()
        self._set_buttons(True)
        self._worker_thread = threading.Thread(target=self._pass2_worker, args=(selected_ids,), daemon=True)
        self._worker_thread.start()

    def _pass2_worker(self, target_ids: list[str]):
        from diary_core.entity_tracker import run_pass2
        from mcp_server.db import get_db_connection
        output_dir    = self.output_dir_var.get().strip()
        days_before   = self.days_before_var.get()
        days_after    = self.days_after_var.get()
        ctx_window    = self.context_window_var.get()
        chunk_overlap = self.chunk_overlap_var.get()
        run_tab       = self.app.run_tab
        max_tokens    = run_tab.max_tokens_var.get()
        api_base      = run_tab.api_base_var.get().strip()
        model         = run_tab.model_var.get().strip()

        self._log("â•â•â• STEP 2 â€” Contextual Profile Generation â•â•â•")
        self._log(f"  Context window: {ctx_window:,} tokens | Chunk overlap: {chunk_overlap} entries")

        db_path = Path(output_dir) / "data" / "diary.db"
        try:
            conn = get_db_connection(str(db_path))
        except Exception:
            self._log(f"âœ— Could not connect to database at: {db_path}")
            self._log("  Make sure the project root contains data/diary.db.")
            self.after(0, lambda: self._set_buttons(False))
            return

        try:
            run_pass2(
                db_conn               = conn,
                output_dir            = output_dir,
                api_url               = api_base,
                model                 = model,
                log                   = self._log,
                stop_event            = self._stop_event,
                n_before              = days_before,
                n_after               = days_after,
                max_tokens            = max_tokens,
                context_window_tokens = ctx_window,
                chunk_overlap         = chunk_overlap,
                target_ids            = target_ids,
            )
            self._log("\nâœ“ Step 2 complete.")
        except Exception:
            import traceback
            self._log(f"\nâœ— Error:\n{traceback.format_exc()}")
        finally:
            conn.close()
            self.after(0, lambda: self._set_buttons(False))
