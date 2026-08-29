"""
app.py
======
Root window and shared theme for Diary Ingestor.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ── Shared dark theme ─────────────────────────────────────────────────────

DARK = {
    "bg":      "#1e1e2e",
    "bg2":     "#181825",
    "bg3":     "#313244",
    "fg":      "#cdd6f4",
    "accent":  "#89b4fa",
    "green":   "#a6e3a1",
    "yellow":  "#f9e2af",
    "red":     "#f38ba8",
    "cyan":    "#89dceb",
    "mauve":   "#cba6f7",
    "subtext": "#a6adc8",
}


def apply_style(root: tk.Tk | tk.Toplevel):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TNotebook",     background=DARK["bg"],  borderwidth=0)
    style.configure("TNotebook.Tab", background=DARK["bg3"], foreground=DARK["fg"],
                    padding=[14, 5], font=("Helvetica", 10, "bold"))
    style.map("TNotebook.Tab",
              background=[("selected", DARK["accent"])],
              foreground=[("selected", DARK["bg"])])
    style.configure("TFrame",      background=DARK["bg"])
    style.configure("TLabelframe", background=DARK["bg"], foreground=DARK["fg"])
    style.configure("TLabelframe.Label", background=DARK["bg"], foreground=DARK["accent"],
                    font=("Helvetica", 10, "bold"))
    style.configure("TButton", background=DARK["accent"], foreground=DARK["bg"],
                    font=("Helvetica", 10, "bold"), padding=6)
    style.map("TButton", background=[("active", DARK["mauve"])])
    style.configure("TLabel",    background=DARK["bg"], foreground=DARK["fg"])
    style.configure("TEntry",    fieldbackground=DARK["bg3"], foreground=DARK["fg"],
                    insertbackground=DARK["fg"])
    style.configure("TCheckbutton", background=DARK["bg"], foreground=DARK["fg"],
                    font=("Helvetica", 10))
    style.map("TCheckbutton", background=[("active", DARK["bg"])])
    style.configure("TRadiobutton", background=DARK["bg"], foreground=DARK["fg"])
    style.map("TRadiobutton", background=[("active", DARK["bg"])])
    style.configure("TProgressbar", troughcolor=DARK["bg3"], background=DARK["green"])
    style.configure("TCombobox",    fieldbackground=DARK["bg3"], foreground=DARK["fg"],
                    selectbackground=DARK["accent"], selectforeground=DARK["bg"])


class IngestorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Diary Ingestor")
        self.geometry("1000x740")
        self.minsize(800, 600)
        self.configure(bg=DARK["bg"])
        apply_style(self)

        # Instance-level staging area — NOT class-level to avoid shared-state bugs
        self._staged: list[dict] = []
        self._staged_labels: list[tuple[str, int]] = []  # [(source_label, entry_count)]

        self._build()

    def _build(self):
        # ── Status bar ──
        self._status_var = tk.StringVar(value="Ready.")
        status_bar = tk.Label(
            self, textvariable=self._status_var,
            bg=DARK["bg2"], fg=DARK["green"],
            anchor="w", padx=10, pady=4,
            font=("Helvetica", 9),
        )
        status_bar.pack(side="bottom", fill="x")

        # ── Notebook ──
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        from .tab_owui import OWUITab
        from .tab_gemini import GeminiTab
        from .tab_old_diary import OldDiaryTab
        from .tab_assemble import AssembleTab

        self.owui_tab    = OWUITab(nb, self)
        self.gemini_tab  = GeminiTab(nb, self)
        self.old_tab     = OldDiaryTab(nb, self)
        self.assemble_tab = AssembleTab(nb, self)

        nb.add(self.owui_tab,     text="  📥 OWUI  ")
        nb.add(self.gemini_tab,   text="  🧠 Gemini  ")
        nb.add(self.old_tab,      text="  📜 Legacy Diary  ")
        nb.add(self.assemble_tab, text="  🗂️ Assemble  ")

    def set_status(self, msg: str):
        self._status_var.set(msg)
        self.update_idletasks()

    # ── Staging area (shared between tabs and assembler tab) ──

    def stage_entries(self, entries: list[dict], source_label: str):
        """Called by each tab to stage entries for assembly."""
        self._staged.extend(entries)
        self._staged_labels.append((source_label, len(entries)))
        self.set_status(f"Staged {len(entries)} entries from {source_label}. Total staged: {len(self._staged)}")
        self.assemble_tab.refresh_staged_count()

    def get_staged(self) -> list[dict]:
        return list(self._staged)

    def get_staged_labels(self) -> list[tuple[str, int]]:
        return list(self._staged_labels)

    def clear_staged(self):
        self._staged.clear()
        self._staged_labels.clear()
        self.assemble_tab.refresh_staged_count()
