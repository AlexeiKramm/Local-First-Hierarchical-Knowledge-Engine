"""
tab_cost.py
===========
Cost Estimator tab — integrates char_token_calculator.py logic with OpenRouter pricing.

Sections:
  A — Directory scan (input token estimation)
  B — Run projection (avg output tokens + summary count)
  C — Pricing ($/M tokens for input and output)
  D — Results table (total cost breakdown)
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

from .app import DARK


class CostTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
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
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))

        # ── Section A — Input Analysis ─────────────────────────────────────────
        sec_a = ttk.LabelFrame(sf, text="A — Input Analysis", padding=10)
        sec_a.pack(fill="x", padx=14, pady=(14, 6))

        # ── Section A — Input Analysis ─────────────────────────────────────────
        sec_a = ttk.LabelFrame(sf, text="A — Input Analysis", padding=10)
        sec_a.pack(fill="x", padx=14, pady=(14, 6))

        self.source_mode_var = tk.StringVar(value="directory")
        tk.Radiobutton(sec_a, text="Directory Scan (raw_entries/)", variable=self.source_mode_var, value="directory",
                       bg=DARK["bg"], fg=DARK["fg"], selectcolor=DARK["bg3"], command=self._on_source_mode_change).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Radiobutton(sec_a, text="Single File (merged_diary.json)", variable=self.source_mode_var, value="file",
                       bg=DARK["bg"], fg=DARK["fg"], selectcolor=DARK["bg3"], command=self._on_source_mode_change).grid(row=0, column=2, sticky="w")

        self.dir_label = tk.Label(sec_a, text="Directory:", bg=DARK["bg"], fg=DARK["fg"])
        self.dir_label.grid(row=1, column=0, sticky="w", pady=(8,0))
        self.dir_var = tk.StringVar()
        self.dir_entry = ttk.Entry(sec_a, textvariable=self.dir_var, width=50)
        self.dir_entry.grid(row=1, column=1, padx=8, sticky="w", pady=(8,0))
        self.dir_btn = ttk.Button(sec_a, text="Browse…", command=self._browse_dir)
        self.dir_btn.grid(row=1, column=2, padx=4, pady=(8,0))

        self.file_label = tk.Label(sec_a, text="File Path:", bg=DARK["bg"], fg=DARK["fg"])
        self.file_label.grid(row=2, column=0, sticky="w", pady=(4,0))
        self.file_var = tk.StringVar()
        self.file_entry = ttk.Entry(sec_a, textvariable=self.file_var, width=50, state="disabled")
        self.file_entry.grid(row=2, column=1, padx=8, sticky="w", pady=(4,0))
        self.file_btn = ttk.Button(sec_a, text="Browse…", command=self._browse_file, state="disabled")
        self.file_btn.grid(row=2, column=2, padx=4, pady=(4,0))

        tk.Label(sec_a, text="Extension filter:", bg=DARK["bg"], fg=DARK["fg"]).grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.ext_var = tk.StringVar(value=".json")
        self.ext_entry = ttk.Entry(sec_a, textvariable=self.ext_var, width=10)
        self.ext_entry.grid(row=3, column=1, padx=8, sticky="w", pady=(8, 0))

        ttk.Button(sec_a, text="🔍 Analyze Input", command=self._scan).grid(row=3, column=2, padx=4, pady=(8, 0))

        self.scan_result_var = tk.StringVar(value="")
        tk.Label(sec_a, textvariable=self.scan_result_var, bg=DARK["bg"], fg=DARK["fg"],
                 font=("Courier", 9), wraplength=600, justify="left",
                 ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))

        self._total_files = 0
        self._total_tokens_est = 0

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _on_source_mode_change(self):
        mode = self.source_mode_var.get()
        if mode == "directory":
            self.dir_entry.config(state="normal")
            self.dir_btn.config(state="normal")
            self.file_entry.config(state="disabled")
            self.file_btn.config(state="disabled")
            self.ext_entry.config(state="normal")
        else:
            self.dir_entry.config(state="disabled")
            self.dir_btn.config(state="disabled")
            self.file_entry.config(state="normal")
            self.file_btn.config(state="normal")
            self.ext_entry.config(state="disabled")

    def _browse_dir(self):
        d = filedialog.askdirectory(title="Select raw entries directory")
        if d:
            self.dir_var.set(d)

    def _browse_file(self):
        f = filedialog.askopenfilename(title="Select merged JSON file", filetypes=[("JSON files", "*.json")])
        if f:
            self.file_var.set(f)

    def _scan(self):
        mode = self.source_mode_var.get()
        total_chars = 0
        unit_count = 0

        if mode == "directory":
            directory = self.dir_var.get().strip()
            if not directory or not os.path.isdir(directory):
                messagebox.showerror("Error", "Please select a valid directory.")
                return

            ext = self.ext_var.get().strip()
            for root, _, files in os.walk(directory):
                for fname in files:
                    if not ext or fname.endswith(ext):
                        fpath = os.path.join(root, fname)
                        try:
                            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                                content = f.read()
                            total_chars += len(content)
                            unit_count += 1
                        except Exception:
                            pass
            source_label = f"Files found: {unit_count:,}"
        else:
            fpath = self.file_var.get().strip()
            if not fpath or not os.path.isfile(fpath):
                messagebox.showerror("Error", "Please select a valid JSON file.")
                return
            
            try:
                import json
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # If it's the merged JSON, it's typically a list of day objects
                if isinstance(data, list):
                    unit_count = len(data)
                    # Estimate char count by dumping it or summing field lengths
                    # Dumping is safer to get a real sense of content volume
                    total_chars = len(json.dumps(data, ensure_ascii=False))
                elif isinstance(data, dict):
                    # Maybe it's a map dating back to keys
                    unit_count = len(data)
                    total_chars = len(json.dumps(data, ensure_ascii=False))
                else:
                    unit_count = 1
                    total_chars = len(json.dumps(data, ensure_ascii=False))
            except Exception as exc:
                messagebox.showerror("Error", f"Failed to parse JSON file: {exc}")
                return
            source_label = f"Entries in file: {unit_count:,}"

        # ~3.5 chars per token (conservative estimate)
        chars_per_token = 3.5
        est_tokens = int(total_chars / chars_per_token)
        avg_tokens_per_unit = int(est_tokens / unit_count) if unit_count else 0

        self.num_summaries_var.set(unit_count)
        self.avg_input_var.set(avg_tokens_per_unit)

        self.scan_result_var.set(
            f"{source_label}  |  "
            f"Total characters: {total_chars:,}  |  "
            f"Estimated input tokens: {est_tokens:,}  |  "
            f"Avg per unit: {avg_tokens_per_unit:,} tokens"
        )

    def _calculate(self):
        n = self.num_summaries_var.get()
        avg_in = self.avg_input_var.get()
        avg_out = self.avg_output_var.get()
        price_in = self.price_in_var.get()
        price_out = self.price_out_var.get()

        if n <= 0:
            messagebox.showwarning("Warning", "Number of summaries is 0. Please scan a directory first.")
            return

        total_input_tokens = n * avg_in
        total_output_tokens = n * avg_out
        cost_in = (total_input_tokens / 1_000_000) * price_in
        cost_out = (total_output_tokens / 1_000_000) * price_out
        total_cost = cost_in + cost_out

        # Clear and populate results tree
        for row in self.result_tree.get_children():
            self.result_tree.delete(row)

        rows = [
            ("Summaries to generate",        f"{n:,}"),
            ("Avg input tokens / summary",   f"{avg_in:,}"),
            ("Avg output tokens / summary",  f"{avg_out:,}"),
            ("Total input tokens",           f"{total_input_tokens:,}"),
            ("Total output tokens",          f"{total_output_tokens:,}"),
            ("Input price ($/M)",            f"${price_in:.4f}"),
            ("Output price ($/M)",           f"${price_out:.4f}"),
            ("Total input cost",             f"${cost_in:.4f}"),
            ("Total output cost",            f"${cost_out:.4f}"),
            ("━━━ TOTAL COST ESTIMATE ━━━",  f"${total_cost:.4f}  (~${total_cost * 1.2:.4f} with 20% buffer)"),
        ]
        for key, val in rows:
            self.result_tree.insert("", "end", values=(key, val))
