"""
tab_browse.py
=============
Tab 3 — Browse Results.

Features:
  - Tree view: Year → Month → Week → Day
  - Detail panel: summary, themes, tone, events, questions, entities
  - Entity Dossier view: click an entity to see their full timeline
  - Entity Synthesis button: run the synthesis prompt on a dossier
  - Export selected unit to markdown / plain text
"""

from __future__ import annotations

import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .app import DARK


class BrowseTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._output_dir: str = ""
        self._all_summaries: dict[str, dict] = {}
        self._current_summary: dict | None = None

        # Declare UI elements to appease static analysis
        self.tree: ttk.Treeview
        self.entity_listbox: tk.Listbox
        self.detail_nb: ttk.Notebook
        self._summary_frame: ttk.Frame
        self._entity_frame: ttk.Frame
        
        self.period_var: tk.StringVar
        self.meta_var: tk.StringVar
        self.energy_var: tk.StringVar
        self.social_var: tk.StringVar
        self.momentum_var: tk.StringVar
        self.tone_var: tk.StringVar
        self.detail_text: scrolledtext.ScrolledText
        
        self.dossier_label_var: tk.StringVar
        self.dossier_text: scrolledtext.ScrolledText

        self._build()

    def _build(self):
        # ── Main pane: tree on the left, details on the right ──
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=10, pady=10)

        # ── Left: tree + entity list ──
        left_frame = ttk.Frame(pane)
        pane.add(left_frame, weight=1)

        # Reload button
        top_ctrl = ttk.Frame(left_frame)
        top_ctrl.pack(fill="x", pady=(0, 4))
        ttk.Button(top_ctrl, text="⟳ Reload", command=self._reload).pack(side="left")
        ttk.Button(top_ctrl, text="📂 Open folder…", command=self._open_folder).pack(side="left", padx=6)

        # Treeview
        tree_frame = ttk.LabelFrame(left_frame, text="Summary Tree", padding=4)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(tree_frame, selectmode="browse", show="tree")
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        tree_scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # Entity dossier list
        entity_frame = ttk.LabelFrame(left_frame, text="Entity Dossiers", padding=4)
        entity_frame.pack(fill="x", pady=(6, 0))

        self.entity_listbox = tk.Listbox(
            entity_frame, bg=DARK["bg2"], fg=DARK["fg"],
            font=("Helvetica", 10), relief="flat", height=5,
            selectbackground=DARK["accent"], selectforeground=DARK["bg"],
        )
        self.entity_listbox.pack(fill="x")
        self.entity_listbox.bind("<<ListboxSelect>>", self._on_entity_select)

        # ── Right: detail area ──
        right_frame = ttk.Frame(pane)
        pane.add(right_frame, weight=2)

        # Tab switcher: Summary | Entity Dossier
        self.detail_nb = ttk.Notebook(right_frame)
        self.detail_nb.pack(fill="both", expand=True)

        self._summary_frame = ttk.Frame(self.detail_nb)
        self._entity_frame  = ttk.Frame(self.detail_nb)
        self.detail_nb.add(self._summary_frame, text="  Summary  ")
        self.detail_nb.add(self._entity_frame,  text="  Entity Dossier  ")

        self._build_summary_panel()
        self._build_entity_panel()

    # ── Summary detail panel ───────────────────────────────────────────────

    def _build_summary_panel(self):
        p = self._summary_frame

        # Header labels
        self.period_var = tk.StringVar(value="Select a node in the tree →")
        tk.Label(p, textvariable=self.period_var, bg=DARK["bg"], fg=DARK["accent"],
                 font=("Helvetica", 13, "bold"), anchor="w").pack(fill="x", padx=8, pady=(8, 0))

        self.meta_var = tk.StringVar(value="")
        tk.Label(p, textvariable=self.meta_var, bg=DARK["bg"], fg=DARK["cyan"],
                 font=("Courier", 8), anchor="w").pack(fill="x", padx=8, pady=(0, 6))

        # Scalar ratings
        scalars_frame = ttk.Frame(p)
        scalars_frame.pack(fill="x", padx=8, pady=(0, 6))
        self.energy_var      = tk.StringVar(value="")
        self.social_var      = tk.StringVar(value="")
        self.momentum_var    = tk.StringVar(value="")
        self.tone_var        = tk.StringVar(value="")
        for label, var in [("⚡ Energy:", self.energy_var), ("👥 Social:", self.social_var),
                           ("🚀 Momentum:", self.momentum_var), ("🎭 Tone:", self.tone_var)]:
            f = ttk.Frame(scalars_frame)
            f.pack(side="left", padx=(0, 16))
            tk.Label(f, text=label, bg=DARK["bg"], fg=DARK["fg"],
                     font=("Helvetica", 9)).pack(anchor="w")
            tk.Label(f, textvariable=var, bg=DARK["bg"], fg=DARK["mauve"],
                     font=("Helvetica", 10, "bold")).pack(anchor="w")

        # Main text area (summary + sections)
        self.detail_text = scrolledtext.ScrolledText(
            p, wrap="word", bg=DARK["bg2"], fg=DARK["fg"],
            font=("Helvetica", 10), relief="flat",
            insertbackground=DARK["fg"], state="disabled",
        )
        self.detail_text.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        # Export controls
        export_frame = ttk.Frame(p)
        export_frame.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(export_frame, text="Export → Markdown", command=self._export_md).pack(side="left")
        ttk.Button(export_frame, text="Export → Text",     command=self._export_txt).pack(side="left", padx=6)

        self._current_summary: dict | None = None

    # ── Entity Dossier panel ───────────────────────────────────────────────

    def _build_entity_panel(self):
        p = self._entity_frame

        self.dossier_label_var = tk.StringVar(value="Select an entity →")
        tk.Label(p, textvariable=self.dossier_label_var, bg=DARK["bg"], fg=DARK["accent"],
                 font=("Helvetica", 13, "bold"), anchor="w").pack(fill="x", padx=8, pady=(8, 0))

        self.dossier_text = scrolledtext.ScrolledText(
            p, wrap="word", bg=DARK["bg2"], fg=DARK["fg"],
            font=("Helvetica", 10), relief="flat",
            insertbackground=DARK["fg"], state="disabled",
        )
        self.dossier_text.pack(fill="both", expand=True, padx=8, pady=(6, 4))

        synth_frame = ttk.Frame(p)
        synth_frame.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(synth_frame, text="✨ Synthesize Entity Arc (LLM)",
                   command=self._synthesize_entity).pack(side="left")
        ttk.Button(synth_frame, text="Export Dossier → Markdown",
                   command=self._export_dossier_md).pack(side="left", padx=6)

    # ── Load data ──────────────────────────────────────────────────────────

    def load_from_directory(self, directory: str):
        """Load all summary JSON files from the given output directory."""
        self._output_dir = directory
        self._reload()

    def _reload(self):
        if not self._output_dir:
            return
        self.tree.delete(*self.tree.get_children())
        self.entity_listbox.delete(0, tk.END)

        # Build tree from files
        levels = ["year", "month", "week", "day"]
        all_summaries: dict[str, dict] = {}

        for level in levels:
            level_dir = Path(self._output_dir) / level
            if not level_dir.exists():
                continue
            for path in sorted(level_dir.glob("*.json")):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    all_summaries[f"{level}::{path.stem}"] = data
                except Exception:
                    continue

        self._all_summaries = all_summaries
        self._populate_tree(all_summaries)

        # Load entity dossiers
        entity_dir = Path(self._output_dir) / "entities"
        if entity_dir.exists():
            for p in sorted(entity_dir.glob("*.json")):
                self.entity_listbox.insert(tk.END, p.stem)

    def _open_folder(self):
        d = filedialog.askdirectory(title="Select analysis output folder")
        if d:
            self.load_from_directory(d)

    def _populate_tree(self, all_summaries: dict[str, dict]):
        """Build a Year→Month→Week→Day hierarchy in the Treeview."""
        years: dict[str, str] = {}    # "YYYY" → tree_id
        months: dict[str, str] = {}   # "YYYY-MM" → tree_id
        weeks: dict[str, str] = {}    # "YYYY-WXX" → tree_id

        # Insert in hierarchical order
        for key, data in sorted(all_summaries.items()):
            level, period = key.split("::", 1)
            label = self._tree_label(data, level, period)

            if level == "year":
                iid = self.tree.insert("", "end", iid=key, text=f"📅 {label}")
                years[period[:4]] = iid
            elif level == "month":
                year_key = period[:4]
                parent = years.get(year_key, "")
                iid = self.tree.insert(parent, "end", iid=key, text=f"📆 {label}")
                months[period[:7]] = iid
            elif level == "week":
                # Find parent month: week key format "YYYY-WXX"
                year_part = period[:4]
                parent = months.get(year_part) or years.get(year_part, "")
                iid = self.tree.insert(parent, "end", iid=key, text=f"🗓 {label}")
                weeks[period] = iid
            elif level == "day":
                month_key = period[:7]
                parent = months.get(month_key, "")
                self.tree.insert(parent, "end", iid=key, text=f"📝 {label}")

    def _tree_label(self, data: dict, level: str, period: str) -> str:
        tone = data.get("emotional_tone", "")
        if tone:
            return f"{period}  [{tone}]"
        return period

    # ── Selection handlers ─────────────────────────────────────────────────

    def _on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        key = sel[0]
        data = self._all_summaries.get(key)
        if data:
            self._show_summary(data)
            self.detail_nb.select(0)   # switch to Summary tab

    def _show_summary(self, data: dict):
        self._current_summary = data

        level = data.get("unit", "?")
        start = data.get("period_start", "?")
        end   = data.get("period_end", "?")
        model = data.get("model_used", "?")
        mode  = data.get("mode", "?")
        t_in  = data.get("token_count_input", "?")
        t_sec = data.get("processing_time_seconds", "?")

        self.period_var.set(f"{level.upper()}: {start}  →  {end}")
        self.meta_var.set(f"model: {model}  |  mode: {mode}  |  ~{t_in} tokens in  |  {t_sec}s")

        energy   = data.get("energy_level")
        social   = data.get("social_connectedness")
        momentum = data.get("forward_momentum")
        tone     = data.get("emotional_tone", "")
        self.energy_var.set(f"{'⭐' * energy if energy else '—'} ({energy}/5)" if energy else "—")
        self.social_var.set(f"{'⭐' * social if social else '—'} ({social}/5)" if social else "—")
        self.momentum_var.set(f"{'⭐' * momentum if momentum else '—'} ({momentum}/5)" if momentum else "—")
        self.tone_var.set(tone or "—")

        # Build detail text
        lines = []
        if data.get("summary"):
            lines += ["── SUMMARY ──────────────────────────", data["summary"], ""]
# Removed central_themes as it is no longer in schema
        if data.get("key_events"):
            lines.append("── KEY EVENTS ──────────────────────")
            for e in data["key_events"]:
                lines.append(f"  • {e}")
            lines.append("")
        if data.get("questions_raised"):
            lines.append("── QUESTIONS RAISED ─────────────────")
            for q in data["questions_raised"]:
                lines.append(f"  ? {q}")
            lines.append("")
        if data.get("entities"):
            lines.append("── ENTITIES ─────────────────────────")
            for name, note in data["entities"].items():
                lines.append(f"  👤 {name}: {note}")
            lines.append("")
        if data.get("retrospective_note"):
            lines += ["── RETROSPECTIVE NOTE ────────────────", data["retrospective_note"], ""]
            
        if data.get("peak_moment"):
            lines += ["── PEAK MOMENT ───────────────────────", data["peak_moment"], ""]
        if data.get("narrative_threads"):
            lines += ["── NARRATIVE THREADS ─────────────────", data["narrative_threads"], ""]
        if data.get("scalar_metrics"):
            lines += ["── SCALAR METRICS ────────────────────", data["scalar_metrics"], ""]
        if data.get("significant_delta"):
            lines += ["── SIGNIFICANT DELTA ─────────────────", data["significant_delta"], ""]
        if data.get("physiological_flags"):
            lines += ["── PHYSIOLOGICAL FLAGS ───────────────", data["physiological_flags"], ""]
        if data.get("relational_map"):
            lines += ["── RELATIONAL MAP ────────────────────", data["relational_map"], ""]
        if data.get("entity_mentions"):
            lines += ["── ENTITY MENTIONS ───────────────────", data["entity_mentions"], ""]
        if data.get("avoidance_signals"):
            lines += ["── AVOIDANCE SIGNALS ─────────────────", data["avoidance_signals"], ""]
        if data.get("growth_markers"):
            lines += ["── GROWTH MARKERS ────────────────────", data["growth_markers"], ""]
        if data.get("coping_mechanisms"):
            lines += ["── COPING MECHANISMS ─────────────────", data["coping_mechanisms"], ""]
        if data.get("self_perception_snapshot"):
            lines += ["── SELF-PERCEPTION SNAPSHOT ──────────", data["self_perception_snapshot"], ""]
        if data.get("values_in_tension"):
            lines += ["── VALUES IN TENSION ─────────────────", data["values_in_tension"], ""]
        if data.get("context_bridge"):
            lines += ["── CONTEXT BRIDGE ────────────────────", data["context_bridge"], ""]

        if data.get("thinking_trace"):
            lines += ["── 🧠 THINKING TRACE (collapsed) ─────",
                      "(See raw JSON for full trace)", ""]

        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", "\n".join(lines))
        self.detail_text.configure(state="disabled")

    # ── Entity dossier ─────────────────────────────────────────────────────

    def _on_entity_select(self, _event=None):
        sel = self.entity_listbox.curselection()
        if not sel:
            return
        label = self.entity_listbox.get(sel[0])
        entity_path = Path(self._output_dir) / "entities" / f"{label}.json"
        if not entity_path.exists():
            return

        with open(entity_path, "r", encoding="utf-8") as f:
            dossier = json.load(f)

        self.dossier_label_var.set(f"Entity: {dossier.get('entity_label', label)}")
        aliases = dossier.get("aliases", [])
        entries = dossier.get("entries", [])
        synthesis = dossier.get("synthesis")

        lines = []
        if aliases:
            lines.append(f"Aliases: {', '.join(aliases)}\n")
        lines.append(f"Total mentions: {len(entries)}\n")
        lines.append("── CHRONOLOGICAL TIMELINE ───────────────────")
        for e in entries:
            lines.append(f"\n[{e['period_start']}] {e['note']}")
        if synthesis:
            lines += ["", "── SYNTHESIS ───────────────────────────────", synthesis]

        self.dossier_text.configure(state="normal")
        self.dossier_text.delete("1.0", "end")
        self.dossier_text.insert("1.0", "\n".join(lines))
        self.dossier_text.configure(state="disabled")
        self.detail_nb.select(1)   # switch to Entity Dossier tab

    def _synthesize_entity(self):
        sel = self.entity_listbox.curselection()
        if not sel:
            messagebox.showwarning("No entity selected", "Please select an entity from the list first.")
            return
        label = self.entity_listbox.get(sel[0])
        entity_path = Path(self._output_dir) / "entities" / f"{label}.json"
        if not entity_path.exists():
            return

        with open(entity_path, "r", encoding="utf-8") as f:
            dossier_data = json.load(f)

        from ..llm_client import LLMClient
        from ..summarizer import SummarizationConfig, Summarizer
        from ..progress_manager import ProgressManager

        run_tab = self.app.run_tab
        client = LLMClient(
            api_base=run_tab.api_base_var.get().strip(),
            model_name=run_tab.model_var.get().strip(),
        )
        cfg = SummarizationConfig()
        progress = ProgressManager(self._output_dir)
        summarizer = Summarizer(client=client, progress=progress, config=cfg)

        entries = dossier_data.get("entries", [])
        if not entries:
            messagebox.showinfo("Empty dossier", "No timeline entries found for this entity.")
            return

        self.app.set_status(f"Running synthesis for entity '{label}'…")
        synthesis_text = summarizer.synthesize_entity(label, entries)

        # Save synthesis back to the dossier file
        dossier_data["synthesis"] = synthesis_text
        with open(entity_path, "w", encoding="utf-8") as f:
            json.dump(dossier_data, f, ensure_ascii=False, indent=2)

        self.app.set_status(f"Synthesis complete for '{label}'.")
        self._on_entity_select()   # refresh display

    # ── Export ─────────────────────────────────────────────────────────────

    def _export_md(self):
        if not self._current_summary:
            return
        text = self._build_export_text(self._current_summary, fmt="md")
        self._save_export(text, default_ext=".md")

    def _export_txt(self):
        if not self._current_summary:
            return
        text = self._build_export_text(self._current_summary, fmt="txt")
        self._save_export(text, default_ext=".txt")

    def _export_dossier_md(self):
        content = self.dossier_text.get("1.0", "end")
        self._save_export(content, default_ext=".md")

    def _save_export(self, text: str, default_ext: str):
        path = filedialog.asksaveasfilename(
            defaultextension=default_ext,
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"), ("All", "*.*")],
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            messagebox.showinfo("Exported", f"Saved to {path}")

    def _build_export_text(self, data: dict, fmt: str = "md") -> str:
        h1 = "# " if fmt == "md" else ""
        h2 = "## " if fmt == "md" else "── "
        sep = "\n\n" if fmt == "md" else "\n"

        parts = []
        parts.append(f"{h1}{data.get('unit','').upper()}: {data.get('period_start','')} – {data.get('period_end','')}{sep}")
        if data.get("summary"):
            parts.append(f"{h2}Summary\n{data['summary']}{sep}")
        if data.get("central_themes"):
            parts.append(f"{h2}Themes\n{', '.join(data['central_themes'])}{sep}")
        if data.get("emotional_tone"):
            parts.append(f"**Emotional Tone:** {data['emotional_tone']}{sep}")
        if data.get("key_events"):
            items = "\n".join(f"- {e}" for e in data["key_events"])
            parts.append(f"{h2}Key Events\n{items}{sep}")
        if data.get("questions_raised"):
            items = "\n".join(f"- {q}" for q in data["questions_raised"])
            parts.append(f"{h2}Questions Raised\n{items}{sep}")
        if data.get("entities"):
            items = "\n".join(f"- **{k}**: {v}" for k,v in data["entities"].items())
            parts.append(f"{h2}Entities\n{items}{sep}")
        if data.get("retrospective_note"):
            parts.append(f"{h2}Retrospective Note\n{data['retrospective_note']}{sep}")
            
        # --- New index fields ---
        for key, title in [
            ("peak_moment", "Peak Moment"),
            ("narrative_threads", "Narrative Threads"),
            ("scalar_metrics", "Scalar Metrics"),
            ("significant_delta", "Significant Delta"),
            ("physiological_flags", "Physiological Flags"),
            ("relational_map", "Relational Map"),
            ("avoidance_signals", "Avoidance Signals"),
            ("growth_markers", "Growth Markers"),
            ("coping_mechanisms", "Coping Mechanisms"),
            ("self_perception_snapshot", "Self-Perception Snapshot"),
            ("values_in_tension", "Values in Tension"),
            ("context_bridge", "Context Bridge"),
        ]:
            if data.get(key):
                parts.append(f"{h2}{title}\n{data[key]}{sep}")

        return "".join(parts)
