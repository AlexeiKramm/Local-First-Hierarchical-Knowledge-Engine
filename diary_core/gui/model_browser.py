"""
model_browser.py
================
ModelBrowserDialog — a Toplevel window that fetches all available models from
the configured OpenRouter (or compatible) endpoint and displays them in a
searchable, sortable Treeview.

Columns:
  ID              — the model identifier (e.g. openai/gpt-4o)
  Name            — human-readable display name
  Context         — maximum context window in tokens
  $/1k in         — prompt cost per 1 000 tokens (USD)
  $/1k out        — completion cost per 1 000 tokens (USD)
  Max out         — maximum output tokens reported by the provider

Usage:
    dlg = ModelBrowserDialog(parent, client, on_select_callback)
    # on_select_callback(model_id: str) is called when the user picks a model.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional


_DARK = {
    "bg":      "#1e1e2e",
    "bg2":     "#2a2a3d",
    "bg3":     "#313149",
    "fg":      "#cdd6f4",
    "fg2":     "#7f849c",
    "accent":  "#89b4fa",
    "accent2": "#74c7ec",
    "green":   "#a6e3a1",
    "yellow":  "#f9e2af",
    "red":     "#f38ba8",
}


def _fmt_price(value) -> str:
    """Format a per-token price as a per-1k-token cost string."""
    try:
        per_k = float(value) * 1000
        if per_k == 0:
            return "free"
        if per_k < 0.001:
            return f"${per_k:.4f}"
        if per_k < 0.01:
            return f"${per_k:.3f}"
        return f"${per_k:.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_ctx(value) -> str:
    """Format a context-window token count (e.g. 131072 → '128k')."""
    try:
        n = int(value)
        if n >= 1_000_000:
            return f"{n // 1_000_000}M"
        if n >= 1_000:
            return f"{n // 1_000}k"
        return str(n)
    except (TypeError, ValueError):
        return "—"


class ModelBrowserDialog(tk.Toplevel):
    """
    Pop-up window listing all models available at the configured endpoint.
    Fetches data in a background thread so the GUI stays responsive.
    """

    COLUMNS = ("id", "name", "context", "price_in", "price_out", "max_out")
    COL_HEADERS = {
        "id":        ("Model ID",      280, "w"),
        "name":      ("Name",          200, "w"),
        "context":   ("Context",        72, "center"),
        "price_in":  ("$/1k in",        70, "center"),
        "price_out": ("$/1k out",       70, "center"),
        "max_out":   ("Max out",        68, "center"),
    }

    def __init__(
        self,
        parent: tk.Widget,
        client,                          # LLMClient instance
        on_select: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent)
        self.client = client
        self.on_select = on_select
        self._all_rows: list[tuple] = []   # raw row tuples (display values)
        self._raw_ids:  list[str]   = []   # parallel list of original model IDs

        self.title("Model Browser")
        self.geometry("900x560")
        self.configure(bg=_DARK["bg"])
        self.resizable(True, True)

        # Keep on top of the main window
        self.transient(parent)
        self.grab_set()

        self._build()
        self._fetch()

    # ── UI layout ──────────────────────────────────────────────────────────

    def _build(self):
        # ── Top toolbar ────────────────────────────────────────────────────
        top = tk.Frame(self, bg=_DARK["bg"], padx=12, pady=8)
        top.pack(fill="x")

        tk.Label(
            top, text="🔭  Model Browser",
            bg=_DARK["bg"], fg=_DARK["accent"],
            font=("Helvetica", 13, "bold"),
        ).pack(side="left")

        self._status_var = tk.StringVar(value="Fetching models…")
        tk.Label(
            top, textvariable=self._status_var,
            bg=_DARK["bg"], fg=_DARK["fg2"],
            font=("Helvetica", 9, "italic"),
        ).pack(side="left", padx=18)

        ttk.Button(top, text="↺  Refresh", command=self._fetch).pack(side="right", padx=4)

        # ── Search bar ─────────────────────────────────────────────────────
        search_row = tk.Frame(self, bg=_DARK["bg"], padx=12, pady=(0, 6))
        search_row.pack(fill="x")

        tk.Label(
            search_row, text="Filter:", bg=_DARK["bg"], fg=_DARK["fg"],
            font=("Helvetica", 9),
        ).pack(side="left")

        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._apply_filter())
        filter_entry = ttk.Entry(search_row, textvariable=self._filter_var, width=40)
        filter_entry.pack(side="left", padx=8)
        filter_entry.focus_set()

        tk.Label(
            search_row,
            text="(searches ID and Name — case-insensitive)",
            bg=_DARK["bg"], fg=_DARK["fg2"], font=("Helvetica", 8, "italic"),
        ).pack(side="left")

        # ── Treeview ───────────────────────────────────────────────────────
        tree_frame = tk.Frame(self, bg=_DARK["bg"])
        tree_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        style = ttk.Style(self)
        style.theme_use("default")
        style.configure(
            "ModelBrowser.Treeview",
            background=_DARK["bg2"],
            foreground=_DARK["fg"],
            fieldbackground=_DARK["bg2"],
            rowheight=24,
            font=("Courier", 9),
        )
        style.configure(
            "ModelBrowser.Treeview.Heading",
            background=_DARK["bg3"],
            foreground=_DARK["accent"],
            font=("Helvetica", 9, "bold"),
        )
        style.map(
            "ModelBrowser.Treeview",
            background=[("selected", _DARK["bg3"])],
            foreground=[("selected", _DARK["accent"])],
        )

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical")
        self._tree = ttk.Treeview(
            tree_frame,
            columns=self.COLUMNS,
            show="headings",
            style="ModelBrowser.Treeview",
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self._tree.yview)

        for col_id, (header, width, anchor) in self.COL_HEADERS.items():
            self._tree.heading(
                col_id, text=header,
                command=lambda c=col_id: self._sort_by(c),
            )
            self._tree.column(col_id, width=width, anchor=anchor, stretch=(col_id in ("id", "name")))

        self._tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._tree.bind("<Double-1>", self._on_double_click)
        self._tree.bind("<Return>",   self._use_selected)

        # Row tags for zebra-striping
        self._tree.tag_configure("even", background=_DARK["bg2"])
        self._tree.tag_configure("odd",  background=_DARK["bg"])
        self._tree.tag_configure("free", foreground=_DARK["green"])

        # ── Bottom bar ─────────────────────────────────────────────────────
        bot = tk.Frame(self, bg=_DARK["bg"], padx=12, pady=8)
        bot.pack(fill="x")

        self._count_var = tk.StringVar(value="")
        tk.Label(
            bot, textvariable=self._count_var,
            bg=_DARK["bg"], fg=_DARK["fg2"], font=("Helvetica", 8),
        ).pack(side="left")

        ttk.Button(bot, text="✕  Close", command=self.destroy).pack(side="right", padx=4)
        self._use_btn = ttk.Button(
            bot, text="✓  Use selected model",
            command=self._use_selected, state="disabled",
        )
        self._use_btn.pack(side="right", padx=4)

        self._tree.bind("<<TreeviewSelect>>", self._on_selection_change)

    # ── Data fetching ──────────────────────────────────────────────────────

    def _fetch(self):
        """Fetch models in a background thread, then populate the treeview."""
        self._status_var.set("Fetching models…")
        self._tree.delete(*self._tree.get_children())
        self._all_rows.clear()
        self._raw_ids.clear()
        self._count_var.set("")
        self._use_btn.config(state="disabled")

        def _worker():
            try:
                models = self.client.fetch_models_detailed()
                self.after(0, lambda: self._populate(models))
            except Exception as exc:
                self.after(0, lambda: self._on_fetch_error(str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def _populate(self, models: list[dict]):
        """Called on the GUI thread after fetch completes."""
        rows: list[tuple] = []
        ids:  list[str]   = []

        for m in models:
            pricing = m.get("pricing") or {}
            top_p   = m.get("top_provider") or {}

            model_id   = m.get("id", "")
            name       = m.get("name") or model_id
            context    = _fmt_ctx(m.get("context_length"))
            price_in   = _fmt_price(pricing.get("prompt"))
            price_out  = _fmt_price(pricing.get("completion"))
            max_out    = _fmt_ctx(top_p.get("max_completion_tokens") or m.get("max_completion_tokens"))

            rows.append((model_id, name, context, price_in, price_out, max_out))
            ids.append(model_id)

        self._all_rows = rows
        self._raw_ids  = ids
        self._apply_filter()
        self._status_var.set(f"Loaded {len(models)} model(s).")

    def _on_fetch_error(self, msg: str):
        self._status_var.set("Fetch failed.")
        messagebox.showerror("Model fetch failed", msg, parent=self)

    # ── Filter & display ───────────────────────────────────────────────────

    def _apply_filter(self):
        """Re-populate the treeview based on the current filter string."""
        query = self._filter_var.get().lower().strip()
        self._tree.delete(*self._tree.get_children())

        visible = 0
        for i, (row, raw_id) in enumerate(zip(self._all_rows, self._raw_ids)):
            model_id, name = row[0], row[1]
            if query and query not in model_id.lower() and query not in name.lower():
                continue
            tag = "even" if visible % 2 == 0 else "odd"
            if row[3] == "free" and row[4] == "free":
                tag = "free"
            self._tree.insert("", "end", iid=str(i), values=row, tags=(tag,))
            visible += 1

        total = len(self._all_rows)
        self._count_var.set(
            f"Showing {visible} of {total} models"
            if total else ""
        )

    # ── Sorting ────────────────────────────────────────────────────────────

    _sort_state: dict[str, bool] = {}   # col_id → ascending

    def _sort_by(self, col_id: str):
        ascending = not self._sort_state.get(col_id, True)
        self._sort_state[col_id] = ascending

        col_idx = self.COLUMNS.index(col_id)
        children = self._tree.get_children()
        rows = [(self._tree.set(iid, col_id), iid) for iid in children]

        def _sort_key(pair):
            val = pair[0]
            # Numeric sort for token counts (strip k/M suffixes) and prices
            if val in ("—", "free", ""):
                return (1, 0.0, val)
            try:
                if val.endswith("k"):
                    return (0, float(val[:-1]) * 1_000, val)
                if val.endswith("M"):
                    return (0, float(val[:-1]) * 1_000_000, val)
                if val.startswith("$"):
                    return (0, float(val[1:]), val)
                return (0, float(val), val)
            except ValueError:
                return (0, 0.0, val)

        rows.sort(key=_sort_key, reverse=not ascending)
        for pos, (_, iid) in enumerate(rows):
            self._tree.move(iid, "", pos)

    # ── Selection ──────────────────────────────────────────────────────────

    def _on_selection_change(self, _event=None):
        selected = self._tree.selection()
        self._use_btn.config(state="normal" if selected else "disabled")

    def _on_double_click(self, _event=None):
        self._use_selected()

    def _use_selected(self, _event=None):
        selected = self._tree.selection()
        if not selected:
            return
        iid = selected[0]
        model_id = self._tree.set(iid, "id")
        if self.on_select and model_id:
            self.on_select(model_id)
        self.destroy()
