"""
app.py
======
Main Tkinter application shell for the Diary Analysis System.
Assembles six tabs: Config, Run, Browse, Timeline, Entity Profiles, Cost Estimator.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


# ── Warm Claude-inspired dark theme (stone palette) ──────────────────────────
DARK = {
    "bg":      "#1C1917",   # warm near-black (stone-900)
    "bg2":     "#292524",   # card/input background (stone-800)
    "bg3":     "#3C3834",   # subtle borders/active (stone-700)
    "fg":      "#F5F0EB",   # warm off-white
    "fg2":     "#A8A29E",   # muted text (stone-400)
    "accent":  "#D97706",   # Claude amber
    "accent2": "#B45309",   # darker amber (hover/active)
    "cyan":    "#FB923C",   # warm orange accent
    "green":   "#86EFAC",   # soft green (success)
    "red":     "#FCA5A5",   # soft red (error)
    "border":  "#57534E",   # stone-600
    # legacy aliases kept so tabs that reference them don't break
    "yellow":  "#FDE68A",
    "mauve":   "#FDBA74",
}


def apply_theme(root: tk.Tk | tk.Toplevel):
    """Apply the shared warm dark theme to all ttk widgets."""
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TNotebook",        background=DARK["bg"],  borderwidth=0)
    style.configure("TNotebook.Tab",    background=DARK["bg3"], foreground=DARK["fg"],
                    padding=[12, 4],    font=("Helvetica", 10, "bold"))
    style.map("TNotebook.Tab",
              background=[("selected", DARK["accent"])],
              foreground=[("selected", DARK["bg"])])
    style.configure("TFrame",           background=DARK["bg"])
    style.configure("TLabelframe",      background=DARK["bg"],  foreground=DARK["fg"])
    style.configure("TLabelframe.Label",background=DARK["bg"],  foreground=DARK["accent"],
                    font=("Helvetica", 10, "bold"))
    style.configure("TButton",          background=DARK["accent"], foreground=DARK["bg"],
                    font=("Helvetica", 10, "bold"), padding=6)
    style.map("TButton",    background=[("active",   DARK["accent2"]),
                                        ("disabled", DARK["bg3"])])
    style.configure("TLabel",           background=DARK["bg"],  foreground=DARK["fg"])
    style.configure("TEntry",           fieldbackground=DARK["bg3"], foreground=DARK["fg"],
                    insertcolor=DARK["fg"])
    style.configure("TCombobox",        fieldbackground=DARK["bg3"], foreground=DARK["fg"],
                    selectbackground=DARK["bg3"])
    style.configure("TCheckbutton",     background=DARK["bg"],  foreground=DARK["fg"])
    style.configure("TProgressbar",     troughcolor=DARK["bg3"], background=DARK["green"])
    style.configure("TScale",           background=DARK["bg"],  troughcolor=DARK["bg3"])
    style.configure("TSeparator",       background=DARK["border"])
    style.configure("Treeview",         background=DARK["bg2"], foreground=DARK["fg"],
                    fieldbackground=DARK["bg2"], rowheight=24)
    style.configure("Treeview.Heading", background=DARK["bg3"], foreground=DARK["accent"],
                    font=("Helvetica", 9, "bold"))
    style.map("Treeview",               background=[("selected", DARK["accent"])],
                                        foreground=[("selected", DARK["bg"])])
    style.configure("TSpinbox",         fieldbackground=DARK["bg3"], foreground=DARK["fg"],
                    insertcolor=DARK["fg"])


class DiaryAnalyzerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Diary Analysis System")
        self.geometry("1100x820")
        self.minsize(920, 680)
        self.configure(bg=DARK["bg"])

        apply_theme(self)
        self._build_ui()

    def _build_ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        from .tab_config   import ConfigTab
        from .tab_run      import RunTab
        from .tab_browse   import BrowseTab
        from .tab_timeline import TimelineTab
        from .tab_entity   import EntityTab
        from .tab_cost     import CostTab

        self.config_tab   = ConfigTab(nb,   app=self)
        self.run_tab      = RunTab(nb,      app=self)
        self.browse_tab   = BrowseTab(nb,   app=self)
        self.timeline_tab = TimelineTab(nb, app=self)
        self.entity_tab   = EntityTab(nb,   app=self)
        self.cost_tab     = CostTab(nb,     app=self)

        nb.add(self.config_tab,   text="  ⚙  Config  ")
        nb.add(self.run_tab,      text="  ▶  Run  ")
        nb.add(self.browse_tab,   text="  🔍  Browse  ")
        nb.add(self.timeline_tab, text="  📈  Timeline  ")
        nb.add(self.entity_tab,   text="  👤  Entity Profiles  ")
        nb.add(self.cost_tab,     text="  💰  Cost Estimator  ")

        # ── Status bar ──
        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(self, textvariable=self.status_var,
                 bg=DARK["bg2"], fg=DARK["green"],
                 anchor="w", padx=8, pady=4,
                 font=("Helvetica", 9)).pack(fill="x", side="bottom")

    def set_status(self, msg: str):
        self.status_var.set(msg)
        self.update_idletasks()

