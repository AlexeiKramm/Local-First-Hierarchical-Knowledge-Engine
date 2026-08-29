"""
tab_timeline.py
===============
Tab 4 — Timeline Trend Tracking.

Features:
  - Matplotlib integration
  - Plots scalar data over time (Energy, Social Connectedness, Forward Momentum)
  - Allows filtering by unit level (Day, Week, Month)
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
import datetime
from .app import DARK

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

class TimelineTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._build()

    def _build(self):
        if not HAS_MATPLOTLIB:
            lbl = tk.Label(self, text="Matplotlib is not installed.\nPlease run: pip install matplotlib",
                           bg=DARK["bg"], fg=DARK["red"], font=("Helvetica", 12))
            lbl.pack(expand=True)
            return

        ctrl_frame = ttk.Frame(self)
        ctrl_frame.pack(fill="x", padx=14, pady=10)

        ttk.Button(ctrl_frame, text="⟳ Refresh Data", command=self.plot_data).pack(side="left")

        tk.Label(ctrl_frame, text="View Level:", bg=DARK["bg"], fg=DARK["fg"]).pack(side="left", padx=(20, 5))
        self.level_var = tk.StringVar(value="week")
        lvl_combo = ttk.Combobox(ctrl_frame, textvariable=self.level_var, values=["day", "week", "month", "year"],
                                 state="readonly", width=8)
        lvl_combo.pack(side="left")
        lvl_combo.bind("<<ComboboxSelected>>", lambda e: self.plot_data())

        plot_frame = ttk.Frame(self)
        plot_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        # Create Figure
        self.fig = Figure(figsize=(8, 4), dpi=100)
        self.fig.patch.set_facecolor(DARK["bg2"])
        
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(DARK["bg"])
        self.ax.tick_params(colors=DARK["fg"])
        for spine in self.ax.spines.values():
            spine.set_color(DARK["bg3"])

        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        toolbar = NavigationToolbar2Tk(self.canvas, plot_frame)
        toolbar.update()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def plot_data(self):
        if not HAS_MATPLOTLIB:
            return

        # Fetch summaries from Browse tab, or check if loaded
        browse_tab = self.app.browse_tab
        if not hasattr(browse_tab, "_all_summaries") or not browse_tab._all_summaries:
            self.ax.clear()
            self.ax.set_facecolor(DARK["bg"])
            self.ax.text(0.5, 0.5, "No data loaded.\nGo to Browse tab to open an output folder.",
                         color=DARK["accent"], ha="center", va="center", transform=self.ax.transAxes)
            self.canvas.draw()
            return

        level = self.level_var.get()
        
        # Extract matching entries and sort
        data_points = []
        for key, data in browse_tab._all_summaries.items():
            lvl, period = key.split("::", 1)
            if lvl == level:
                try:
                    energy = int(data.get("energy_level") or 0)
                    social = int(data.get("social_connectedness") or 0)
                    momentum = int(data.get("forward_momentum") or 0)
                    
                    if energy == 0 and social == 0 and momentum == 0:
                        continue # Skip empty records
                        
                    data_points.append({
                        "period": period,
                        "energy": energy,
                        "social": social,
                        "momentum": momentum
                    })
                except ValueError:
                    continue

        data_points.sort(key=lambda x: x["period"])

        self.ax.clear()
        self.ax.set_facecolor(DARK["bg"])
        self.ax.tick_params(colors=DARK["fg"])
        for spine in self.ax.spines.values():
            spine.set_color(DARK["bg3"])

        if not data_points:
            self.ax.text(0.5, 0.5, f"No metric data found for level: {level}",
                         color=DARK["accent"], ha="center", va="center", transform=self.ax.transAxes)
            self.canvas.draw()
            return

        periods = [dp["period"] for dp in data_points]
        energies = [dp["energy"] for dp in data_points]
        socials = [dp["social"] for dp in data_points]
        momentums = [dp["momentum"] for dp in data_points]

        x_indices = range(len(periods))

        self.ax.plot(x_indices, energies, marker='o', label="Energy", color=DARK["yellow"], linewidth=2)
        self.ax.plot(x_indices, socials, marker='s', label="Social", color=DARK["green"], linewidth=2)
        self.ax.plot(x_indices, momentums, marker='^', label="Momentum", color=DARK["cyan"], linewidth=2)

        self.ax.set_ylim(0.5, 5.5)
        self.ax.set_yticks([1, 2, 3, 4, 5])
        
        # Format X axis labels: hide some if too many
        step = max(1, len(periods) // 15)
        self.ax.set_xticks(x_indices[::step])
        self.ax.set_xticklabels(periods[::step], rotation=45, ha="right")

        self.ax.set_title(f"Trend Tracking ({level.capitalize()})", color=DARK["fg"], pad=15)
        self.ax.legend(facecolor=DARK["bg3"], edgecolor=DARK["bg3"], labelcolor=DARK["fg"])
        self.ax.grid(True, linestyle="--", alpha=0.3, color=DARK["fg"])

        self.fig.tight_layout()
        self.canvas.draw()
