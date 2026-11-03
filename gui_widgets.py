"""Reusable Tkinter widgets: sortable / filterable table, convergence plot, property form."""
import math
import tkinter as tk
from tkinter import ttk

import gui_fields

ERR_BG = "#fee2e2"


def _num(s):
    try:
        return (0, float(s))
    except (TypeError, ValueError):
        return (1, str(s).lower())


class SortableTable(ttk.Frame):
    """Treeview with click-to-sort headers, a text filter and row-click selection callback."""

    def __init__(self, parent, on_select=None):
        super().__init__(parent)
        self.on_select = on_select
        self.headers, self.rows, self.keys, self.tags = [], [], [], []
        self.sort_col, self.sort_desc = None, False
        self.stale = False
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Filter:").pack(side="left", padx=(2, 4))
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._render())
        ttk.Entry(top, textvariable=self.filter_var, width=28).pack(side="left")
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, show="headings", height=6)
        ys = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(body, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.tree.tag_configure("stale", foreground="#9ca3af")
        self.tree.tag_configure("red", foreground="#dc2626")
        self.tree.tag_configure("amber", foreground="#b45309")
        self.tree.bind("<<TreeviewSelect>>", self._selected)

    def set_data(self, headers, rows, keys, tags=None):
        self.headers, self.rows, self.keys = list(headers), list(rows), list(keys)
        self.tags = list(tags) if tags else [""] * len(rows)
        self.tree.configure(columns=self.headers)
        for i, h in enumerate(self.headers):
            self.tree.heading(h, text=h, command=lambda c=i: self._sort_by(c))
            self.tree.column(h, width=max(70, 9 * len(str(h))), minwidth=50, anchor="center", stretch=True)
        self._render()

    def clear(self):
        self.set_data([], [], [])

    def set_stale(self, stale):
        self.stale = stale
        self._render()

    def _sort_by(self, col):
        self.sort_desc = (not self.sort_desc) if self.sort_col == col else False
        self.sort_col = col
        self._render()

    def _render(self):
        self.tree.delete(*self.tree.get_children())
        order = list(range(len(self.rows)))
        if self.sort_col is not None and self.sort_col < len(self.headers):
            order.sort(key=lambda i: _num(self.rows[i][self.sort_col]), reverse=self.sort_desc)
        text = self.filter_var.get().strip().lower()
        for i in order:
            row = self.rows[i]
            if text and not any(text in str(c).lower() for c in row):
                continue
            tags = ("stale",) if self.stale else ((self.tags[i],) if self.tags[i] else ())
            self.tree.insert("", "end", iid=str(i), values=[str(c) for c in row], tags=tags)

    def _selected(self, _event=None):
        sel = self.tree.selection()
        if sel and self.on_select:
            i = int(sel[0])
            if 0 <= i < len(self.keys):
                self.on_select(*self.keys[i])


class ConvergencePlot(ttk.Frame):
    """log10(max mismatch) against iteration number."""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = None
        try:
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            self.fig = Figure(figsize=(5, 2.3), tight_layout=True)
            self.canvas = FigureCanvasTkAgg(self.fig, master=self)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
            self.clear()
        except Exception as exc:                     # matplotlib missing: degrade gracefully
            ttk.Label(self, text=f"Convergence plot needs matplotlib ({exc})").pack(padx=10, pady=10)

    def _axes(self):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.set_xlabel("Iteration")
        ax.set_ylabel("log10 (max mismatch / pu)")
        ax.grid(True, alpha=0.3)
        return ax

    def clear(self):
        if self.canvas is None:
            return
        ax = self._axes()
        ax.text(0.5, 0.5, "Run a load flow to see convergence", ha="center", va="center",
                transform=ax.transAxes, color="#9ca3af")
        self.canvas.draw_idle()

    def plot(self, results, tolerance=None):
        if self.canvas is None:
            return
        ax = self._axes()
        x = [e["iteration"] for e in results["iteration_log"]]
        y = [math.log10(max(e["max_mismatch_pu"], 1e-20)) for e in results["iteration_log"]]
        ax.plot(x, y, "o-", color="#2563eb")
        if tolerance:
            ax.axhline(math.log10(tolerance), color="#dc2626", ls="--", lw=1, label=f"tolerance {tolerance:g}")
            ax.legend(loc="upper right", fontsize=8)
        ax.set_title("Convergence" + ("" if results["converged"] else " (DIVERGED)"), fontsize=9)
        self.canvas.draw_idle()


class PropertyForm(ttk.Frame):
    """Fields of one element (units shown), validated on apply()."""

    def __init__(self, parent, model, kind, key, on_applied=None):
        super().__init__(parent)
        self.model, self.kind, self.key, self.on_applied = model, kind, key, on_applied
        self.sk = gui_fields.spec_kind(model.net, kind, key)
        self.vars, self.widgets = {}, {}
        values = gui_fields.get_values(model.net, kind, key)
        form = ttk.Frame(self)
        form.pack(fill="x")
        for row, f in enumerate(gui_fields.SPECS[self.sk]):
            unit = f" ({f.unit})" if f.unit else ""
            ttk.Label(form, text=f.label + unit).grid(row=row, column=0, sticky="w", pady=1, padx=(0, 6))
            v = values[f.key]
            if f.type == "readonly":
                w = ttk.Label(form, text=str(v), foreground="#475569")
                var = None
            elif f.type == "bool":
                var = tk.BooleanVar(value=bool(v))
                w = ttk.Checkbutton(form, variable=var)
            elif f.type == "choice":
                var = tk.StringVar(value=str(v))
                w = ttk.Combobox(form, textvariable=var, values=f.choices, state="readonly", width=12)
                w.bind("<<ComboboxSelected>>", lambda _e: self._update_enabled())
            else:
                var = tk.StringVar(value=self._fmt(v))
                w = tk.Entry(form, textvariable=var, width=14)
            w.grid(row=row, column=1, sticky="ew", pady=1)
            self.vars[f.key], self.widgets[f.key] = var, w
        form.columnconfigure(1, weight=1)
        self.derived = ttk.Label(self, text="", wraplength=260, foreground="#475569")
        self.derived.pack(fill="x", pady=(6, 0))
        self.msg = ttk.Label(self, text="", wraplength=260, foreground="#dc2626")
        self.msg.pack(fill="x")
        self._update_enabled()
        self._update_derived()

    @staticmethod
    def _fmt(v):
        return f"{v:.10g}" if isinstance(v, float) else str(v)

    def _update_enabled(self):
        def en(keys, on):
            for k in keys:
                w = self.widgets.get(k)
                if w is not None and k in self.vars and self.vars[k] is not None:
                    w.configure(state="normal" if on else "disabled")
        mode = self.vars.get("input_mode")
        mode = mode.get() if mode is not None else None
        if self.sk == "line":
            en(["length_km", "r_ohm_km", "x_ohm_km", "b_us_km"], mode == "physical")
            en(["r", "x", "b"], mode != "physical")
        elif self.sk == "transformer":
            en(["rated_mva", "z_pct", "x_r"], mode == "nameplate")
            en(["r", "x"], mode != "nameplate")
        elif self.sk == "bus":
            zip_ = self.vars["load_model"].get() == "ZIP"
            en(["zip_z", "zip_i", "zip_p"], zip_)

    def _update_derived(self):
        try:
            self.derived.configure(text=gui_fields.derived_text(self.model.net, self.kind, self.key))
        except Exception:
            self.derived.configure(text="")

    def values(self):
        out = {}
        for k, var in self.vars.items():
            if var is not None:
                out[k] = var.get()
        return out

    def show_errors(self, errs):
        for k, w in self.widgets.items():
            if isinstance(w, tk.Entry):
                try:
                    w.configure(bg=ERR_BG if k in errs else "white")
                except tk.TclError:
                    pass

        labels = {f.key: f.label for f in gui_fields.SPECS[self.sk]}
        self.msg.configure(text="\n".join(
            f"{labels.get(k, k)}: {m}" for k, m in errs.items()
        ))

    def apply(self):
        errs = self.model.apply_fields(self.kind, self.key, self.values())
        self.show_errors(errs)
        if errs:
            return False
        self._update_derived()
        if self.on_applied:
            self.on_applied()
        return True