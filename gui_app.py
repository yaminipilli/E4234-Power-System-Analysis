"""Tkinter desktop GUI for the E4234 Newton-Raphson load-flow simulator (assignment Part B).

All editing logic lives in gui_model.py / gui_fields.py (unit-tested, no Tk); this file only draws
and routes events.  Run with:  python gui_app.py
"""
from __future__ import annotations

import json
import math
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import about_info
import display as D
import report
from gui_model import ProjectModel
from gui_widgets import ConvergencePlot, PropertyForm, SortableTable
from layout import GRID
from matpower_export import save_matpower_case

APP_TITLE = "E4 234 Load Flow Simulator"
DEFAULT_CASE = Path(__file__).parent / "data" / "case14.m"
RECENT_FILE = Path.home() / ".e4234_loadflow_recent.json"
RUN_LABEL = "Run NR\tF5"
PALETTE = ["Select", "Pan", "Bus", "Line", "Transformer", "Generator", "Load", "Shunt", "External grid"]

USER_GUIDE = (
    "QUICK START\n"
    "1. Drag Bus from the palette onto the canvas (or click it, then click the canvas).\n"
    "2. Line / Transformer: pick the tool, then click the two buses to connect.\n"
    "3. Generator, Load, Shunt, External grid: drop onto a bus. Exactly one external grid (slack) is needed.\n"
    "4. Double-click an element (or select it) to edit its properties; values are checked on Apply.\n"
    "5. Set the study case (left), then Run NR (F5). Run is disabled while Network > Check network fails.\n"
    "6. Read results: boxes on the diagram, tables below (click a row to find the element), violations,\n"
    "   iteration log and convergence plot.\n\n"
    "COLOURS\n"
    "Blue / purple bus: below Vmin / above Vmax.  Amber branch: 80-100 % loading, red: above 100 %.\n"
    "Dashed grey: out of service.  Grey results: stale (network edited since the last run).\n"
    "Orange bar: largest mismatch after a divergence.\n\n"
    "MOUSE / KEYS\n"
    "Wheel: zoom.  Middle-drag or Pan tool: pan.  Ctrl+click: multi-select; drag on empty space: box select.\n"
    "Right-click: properties, delete, take out of service.  Del: delete.  F: fit to window.  Esc: cancel tool.\n"
    "Ctrl+Z / Ctrl+Y undo / redo.  Ctrl+C / Ctrl+V copy / paste buses.  Ctrl+F find.  Ctrl+K check network."
)


class LoadFlowApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1450x880")
        self.minsize(1050, 680)
        self.model = ProjectModel()
        self.sel = set()                      # {('bus', n), ('branch', i), ('gen', i)}
        self.tool = "Select"
        self.pending_branch = None
        self.scale, self.ox, self.oy = 1.0, 0.0, 0.0
        self._drag = self._band = self._pan = self._palette_drag = None
        self._redraw_job = None
        self.var_boxes = tk.BooleanVar(value=True)
        self.var_colour = tk.BooleanVar(value=True)
        self.var_grid = tk.BooleanVar(value=True)
        self.var_kv = tk.BooleanVar(value=False)
        self.var_tol = tk.StringVar(value="1e-8")
        self.var_iter = tk.StringVar(value="30")
        self.var_start = tk.StringVar(value="Flat start")
        self.var_qlim = tk.BooleanVar(value=False)
        self.var_scale = tk.StringVar(value="1.0")
        self.status = tk.StringVar(value="Ready")
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._sync_study_to_widgets()
        self.model_changed(fit=True)

    # ------------------------------------------------------------------ construction
    def _build_ui(self):
        self._build_menus()
        self._build_toolbar()
        self.vpane = ttk.Panedwindow(self, orient="vertical")
        self.vpane.pack(fill="both", expand=True)
        self.hpane = ttk.Panedwindow(self.vpane, orient="horizontal")
        self.vpane.add(self.hpane, weight=5)
        left = ttk.Frame(self.hpane, width=170, padding=5)
        center = ttk.Frame(self.hpane, padding=2)
        right = ttk.Frame(self.hpane, width=300, padding=5)
        self.hpane.add(left, weight=0)
        self.hpane.add(center, weight=5)
        self.hpane.add(right, weight=1)
        self._build_palette(left)
        ttk.Label(center, text="SINGLE-LINE DIAGRAM", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.canvas = tk.Canvas(center, background="white", highlightthickness=1, highlightbackground="#cbd5e1")
        self.canvas.pack(fill="both", expand=True)
        self._bind_canvas()
        self._build_properties(right)
        self._build_results()
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w", padding=3).pack(side="bottom", fill="x")
        self._bind_keys()

    def _build_palette(self, parent):
        ttk.Label(parent, text="ELEMENT PALETTE", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(parent, text="Drag onto the canvas (or click, then click the canvas).",
                  wraplength=150, foreground="#475569").pack(anchor="w", pady=(0, 6))
        self.palette_buttons = {}
        for item in PALETTE:
            b = ttk.Button(parent, text=item)
            b.pack(fill="x", pady=1)
            b.bind("<ButtonPress-1>", lambda e, k=item: self._palette_press(k, e))
            b.bind("<B1-Motion>", self._palette_motion)
            b.bind("<ButtonRelease-1>", self._palette_release)
            self.palette_buttons[item] = b
        ttk.Separator(parent).pack(fill="x", pady=8)
        ttk.Label(parent, text="STUDY CASE", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.study_frame = ttk.Frame(parent)
        self.study_frame.pack(fill="x")
        ttk.Label(self.study_frame, text="Tolerance (pu)").pack(anchor="w")
        self.ent_tol = tk.Entry(self.study_frame, textvariable=self.var_tol, width=12)
        self.ent_tol.pack(anchor="w")
        ttk.Label(self.study_frame, text="Maximum iterations").pack(anchor="w", pady=(4, 0))
        self.ent_iter = tk.Entry(self.study_frame, textvariable=self.var_iter, width=12)
        self.ent_iter.pack(anchor="w")
        ttk.Label(self.study_frame, text="Initial point").pack(anchor="w", pady=(4, 0))
        ttk.Combobox(self.study_frame, textvariable=self.var_start, state="readonly", width=17,
                     values=["Flat start", "Previous solution", "Case data"]).pack(anchor="w")
        ttk.Checkbutton(self.study_frame, text="Enforce Q limits", variable=self.var_qlim,
                        command=self._commit_study_quiet).pack(anchor="w", pady=(4, 0))
        ttk.Label(self.study_frame, text="Global load scaling").pack(anchor="w", pady=(4, 0))
        self.ent_scale = tk.Entry(self.study_frame, textvariable=self.var_scale, width=12)
        self.ent_scale.pack(anchor="w")
        self.run_button = ttk.Button(self.study_frame, text="Run NR (F5)", command=self.run_nr)
        self.run_button.pack(fill="x", pady=8)

    def _build_properties(self, parent):
        ttk.Label(parent, text="PROPERTIES", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.prop_title = ttk.Label(parent, text="Nothing selected")
        self.prop_title.pack(anchor="w", pady=(0, 4))
        holder = ttk.Frame(parent)
        holder.pack(fill="both", expand=True)
        self.prop_canvas = tk.Canvas(holder, highlightthickness=0, width=270)
        sb = ttk.Scrollbar(holder, orient="vertical", command=self.prop_canvas.yview)
        self.prop_canvas.configure(yscrollcommand=sb.set)
        self.prop_canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.prop_inner = ttk.Frame(self.prop_canvas)
        self.prop_canvas.create_window((0, 0), window=self.prop_inner, anchor="nw")
        self.prop_inner.bind("<Configure>", lambda _e: self.prop_canvas.configure(scrollregion=self.prop_canvas.bbox("all")))
        self.prop_form = None
        btns = ttk.Frame(parent)
        btns.pack(fill="x", pady=4)
        self.btn_apply = ttk.Button(btns, text="Apply", command=self.apply_properties)
        self.btn_apply.pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(btns, text="Delete", command=self.delete_selected).pack(side="left", fill="x", expand=True)

    def _build_results(self):
        outer = ttk.Frame(self.vpane)
        self.vpane.add(outer, weight=2)
        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill="both", expand=True)
        self.tables = {}
        for name in ("Bus results", "Branch results", "Generators", "Violations"):
            t = SortableTable(self.nb, on_select=self.select_from_table)
            self.nb.add(t, text=name)
            self.tables[name] = t
        logf = ttk.Frame(self.nb)
        self.log = tk.Text(logf, height=8, wrap="none", font=("Consolas", 9), state="normal")
        ys = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=ys.set)
        self.log.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.nb.add(logf, text="Iteration log")
        self.plot = ConvergencePlot(self.nb)
        self.nb.add(self.plot, text="Convergence plot")
        self.log_tab, self.plot_tab = logf, self.plot

    def _build_menus(self):
        bar = tk.Menu(self)
        self.config(menu=bar)
        m = tk.Menu(bar, tearoff=False)
        for label, cmd in [("New\tCtrl+N", self.file_new), ("Open project...\tCtrl+O", self.file_open),
                           ("Save\tCtrl+S", self.file_save), ("Save as...", self.file_save_as), (None, None),
                           ("Open example: IEEE 14-bus", self.open_example),
                           ("Import IEEE CDF / MATPOWER...", self.file_import),
                           ("Export case as MATPOWER...", self.export_matpower),
                           ("Export results (CSV/XLSX)...", self.export_results),
                           ("Export report (PDF/HTML)...", self.export_report), (None, None)]:
            m.add_command(label=label, command=cmd) if label else m.add_separator()
        self.recent_menu = tk.Menu(m, tearoff=False)
        m.add_cascade(label="Recent files", menu=self.recent_menu)
        m.add_separator()
        m.add_command(label="Exit", command=self.on_close)
        bar.add_cascade(label="File", menu=m)
        m = tk.Menu(bar, tearoff=False)
        for label, cmd in [("Undo\tCtrl+Z", self.undo), ("Redo\tCtrl+Y", self.redo), (None, None),
                           ("Copy\tCtrl+C", self.copy_selected), ("Paste\tCtrl+V", self.paste),
                           ("Delete\tDel", self.delete_selected), ("Select all\tCtrl+A", self.select_all), (None, None),
                           ("Find element by name or number...\tCtrl+F", self.find_element)]:
            m.add_command(label=label, command=cmd) if label else m.add_separator()
        bar.add_cascade(label="Edit", menu=m)
        m = tk.Menu(bar, tearoff=False)
        m.add_command(label="Zoom in\tCtrl++", command=lambda: self.zoom_at_centre(1.25))
        m.add_command(label="Zoom out\tCtrl+-", command=lambda: self.zoom_at_centre(0.8))
        m.add_command(label="Fit to window\tF", command=self.fit_view)
        m.add_separator()
        m.add_checkbutton(label="Show result boxes", variable=self.var_boxes, command=self.redraw)
        m.add_checkbutton(label="Colour coding", variable=self.var_colour, command=self.redraw)
        m.add_checkbutton(label="Show grid", variable=self.var_grid, command=self.redraw)
        m.add_checkbutton(label="Show bus voltage in kV", variable=self.var_kv, command=self.redraw)
        m.add_separator()
        m.add_command(label="Reset panel layout", command=self.reset_layout)
        bar.add_cascade(label="View", menu=m)
        m = tk.Menu(bar, tearoff=False)
        m.add_command(label="Check network\tCtrl+K", command=self.check_network)
        m.add_command(label="Auto-layout", command=self.auto_layout)
        m.add_command(label="System base MVA and frequency...", command=self.set_base)
        bar.add_cascade(label="Network", menu=m)
        self.study_menu = tk.Menu(bar, tearoff=False)
        self.study_menu.add_command(label="Load flow settings", command=self.focus_study)
        self.study_menu.add_command(label=RUN_LABEL, command=self.run_nr)
        self.study_menu.add_command(label="Global load scaling...", command=self.ask_scaling)
        self.study_menu.add_checkbutton(label="Q-limit enforcement", variable=self.var_qlim, command=self._commit_study_quiet)
        bar.add_cascade(label="Study", menu=self.study_menu)
        m = tk.Menu(bar, tearoff=False)
        for label, cmd in [("Bus table", lambda: self.show_tab("Bus results")),
                           ("Branch table", lambda: self.show_tab("Branch results")),
                           ("Generator table", lambda: self.show_tab("Generators")),
                           ("Loss summary", self.show_loss_summary),
                           ("Violations list", lambda: self.show_tab("Violations")),
                           ("Convergence plot", lambda: self.nb.select(self.plot_tab))]:
            m.add_command(label=label, command=cmd)
        bar.add_cascade(label="Results", menu=m)
        m = tk.Menu(bar, tearoff=False)
        m.add_command(label="User guide\tF1", command=self.user_guide)
        m.add_command(label="About", command=self.about)
        bar.add_cascade(label="Help", menu=m)

    def _build_toolbar(self):
        tb = ttk.Frame(self, padding=(6, 3))
        tb.pack(side="top", fill="x")
        self.toolbar_buttons = {}
        for label, cmd in [("New", self.file_new), ("Open", self.file_open), ("Save", self.file_save), ("|", None),
                           ("Undo", self.undo), ("Redo", self.redo), ("|", None),
                           ("Select", lambda: self.set_tool("Select")), ("Pan", lambda: self.set_tool("Pan")),
                           ("Zoom-fit", self.fit_view), ("|", None), ("Check network", self.check_network),
                           ("Run NR (F5)", self.run_nr), ("Show results", lambda: self.show_tab("Bus results"))]:
            if cmd is None:
                ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=4)
                continue
            b = ttk.Button(tb, text=label, command=cmd)
            b.pack(side="left", padx=1)
            self.toolbar_buttons[label] = b

    def _bind_canvas(self):
        c = self.canvas
        c.bind("<Button-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_motion)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<Button-3>", self.on_context)
        c.bind("<Button-2>", self.on_pan_start)
        c.bind("<B2-Motion>", self.on_pan_move)
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Button-4>", lambda e: self.zoom_at(e.x, e.y, 1.15))
        c.bind("<Button-5>", lambda e: self.zoom_at(e.x, e.y, 1 / 1.15))
        c.bind("<Configure>", lambda _e: self.schedule_redraw())
        c.bind("<Delete>", lambda _e: self.delete_selected())
        c.bind("<KeyPress-f>", lambda _e: self.fit_view())
        c.bind("<Escape>", lambda _e: self.cancel_tool())

    def _bind_keys(self):
        guard = lambda fn: (lambda _e: None if self._typing() else fn())
        self.bind("<F5>", lambda _e: self.run_nr())
        self.bind("<F1>", lambda _e: self.user_guide())
        self.bind("<Control-n>", lambda _e: self.file_new())
        self.bind("<Control-o>", lambda _e: self.file_open())
        self.bind("<Control-s>", lambda _e: self.file_save())
        self.bind("<Control-k>", lambda _e: self.check_network())
        self.bind("<Control-z>", guard(self.undo))
        self.bind("<Control-y>", guard(self.redo))
        self.bind("<Control-c>", guard(self.copy_selected))
        self.bind("<Control-v>", guard(self.paste))
        self.bind("<Control-a>", guard(self.select_all))
        self.bind("<Control-f>", lambda _e: self.find_element())
        for seq in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.bind(seq, lambda _e: self.zoom_at_centre(1.25))
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            self.bind(seq, lambda _e: self.zoom_at_centre(0.8))

    def _typing(self):
        w = self.focus_get()
        return isinstance(w, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text, ttk.Spinbox))

    # ------------------------------------------------------------------ view transform
    def w2s(self, x, y):
        return x * self.scale + self.ox, y * self.scale + self.oy

    def s2w(self, sx, sy):
        return (sx - self.ox) / self.scale, (sy - self.oy) / self.scale

    def zoom_at(self, sx, sy, factor):
        new = min(max(self.scale * factor, 0.15), 6.0)
        factor = new / self.scale
        self.ox = sx - (sx - self.ox) * factor
        self.oy = sy - (sy - self.oy) * factor
        self.scale = new
        self.redraw()

    def zoom_at_centre(self, factor):
        self.zoom_at(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2, factor)

    def on_wheel(self, e):
        self.zoom_at(e.x, e.y, 1.15 if e.delta > 0 else 1 / 1.15)

    def fit_view(self):
        pos = self.model.positions
        if not pos:
            return
        xs = [p[0] for p in pos.values()]
        ys = [p[1] for p in pos.values()]
        w = max(self.canvas.winfo_width(), 300)
        h = max(self.canvas.winfo_height(), 200)
        bw, bh = max(max(xs) - min(xs), 1) + 200, max(max(ys) - min(ys), 1) + 200
        self.scale = min(w / bw, h / bh, 2.0)
        self.ox = w / 2 - self.scale * (min(xs) + max(xs)) / 2
        self.oy = h / 2 - self.scale * (min(ys) + max(ys)) / 2
        self.redraw()

    def on_pan_start(self, e):
        self._pan = (e.x, e.y, self.ox, self.oy)

    def on_pan_move(self, e):
        if self._pan:
            self.ox = self._pan[2] + e.x - self._pan[0]
            self.oy = self._pan[3] + e.y - self._pan[1]
            self.redraw()

    # ------------------------------------------------------------------ drawing
    def schedule_redraw(self):
        if self._redraw_job is None:
            self._redraw_job = self.after(30, self._do_redraw)

    def _do_redraw(self):
        self._redraw_job = None
        self.redraw()

    def _bus_extent(self, number):
        net = self.model.net
        n_above = sum(1 for g in net.generators if g.bus_number == number)
        b = net.buses[net.index[number]]
        n_below = (1 if (b.p_load or b.q_load) else 0) + (1 if (b.g_shunt or b.b_shunt) else 0)
        w = max(60.0, 34.0 * max(n_above + (1 if b.kind.upper() == "SLACK" else 0), n_below) + 20)
        x, y = self.model.positions[number]
        return x - w / 2, x + w / 2, y

    def _attach(self, a, b, j=0, n=1):
        ax0, ax1, ay = self._bus_extent(a)
        bx0, bx1, by = self._bus_extent(b)
        fan = (j - (n - 1) / 2) * 10
        xa = min(max((bx0 + bx1) / 2, ax0 + 8), ax1 - 8) + fan
        xb = min(max((ax0 + ax1) / 2, bx0 + 8), bx1 - 8) + fan
        return (xa, ay), (xb, by)

    def redraw(self):
        c = self.canvas
        c.delete("all")
        m, net = self.model, self.model.net
        res, stale = m.results, m.stale
        st = self._study_view()
        rb = {r["bus"]: r for r in res["bus_results"]} if res else {}
        worst = set(res.get("worst_buses", [])) if res and not res["converged"] else set()
        if st["grid"] and self.scale > 0.35:
            step = GRID * self.scale * (1 if self.scale > 0.6 else 2)
            w, h = c.winfo_width(), c.winfo_height()
            x = self.ox % step
            while x < w:
                y = self.oy % step
                while y < h:
                    c.create_line(x, y, x + 1, y + 1, fill="#d4d4d8")
                    y += step
                x += step
        # ---- branches
        pairs = {}
        for i, br in enumerate(net.branches):
            pairs.setdefault(frozenset((br.from_bus, br.to_bus)), []).append(i)
        for i, br in enumerate(net.branches):
            if br.from_bus not in m.positions or br.to_bus not in m.positions:
                continue
            twins = pairs[frozenset((br.from_bus, br.to_bus))]
            (x1, y1), (x2, y2) = self._attach(br.from_bus, br.to_bus, twins.index(i), len(twins))
            row = res["branch_results"][i] if res and i < len(res["branch_results"]) else None
            pct = row["loading_percent"] if row and row["active"] else None
            if st["colour"] and res:
                col = D.branch_colour(pct, br.in_service, stale, True)
            else:
                col = D.COL_GREY if not br.in_service else D.COL_NORMAL
            (sx1, sy1), (sx2, sy2) = self.w2s(x1, y1), self.w2s(x2, y2)
            sel = ("branch", i) in self.sel
            c.create_line(sx1, sy1, sx2, sy2, fill=col, width=(4 if sel else 2), dash=(6, 4) if not br.in_service else (),
                          tags=(f"branch:{i}",))
            mx, my = (sx1 + sx2) / 2, (sy1 + sy2) / 2
            if br.kind == "transformer":
                r = 9 * max(self.scale, 0.6)
                c.create_oval(mx - 1.5 * r, my - r, mx + 0.5 * r, my + r, outline=col, width=2, tags=(f"branch:{i}",))
                c.create_oval(mx - 0.5 * r, my - r, mx + 1.5 * r, my + r, outline=col, width=2, tags=(f"branch:{i}",))
            if row and row["active"] and st["boxes"]:
                self._box(mx + 8, my - 6, D.branch_box_text(row), D.COL_GREY if stale else col)
        # ---- buses and attached symbols
        for b in net.buses:
            if b.number not in m.positions:
                continue
            x0, x1, y = self._bus_extent(b.number)
            (sx0, sy), (sx1, _) = self.w2s(x0, y), self.w2s(x1, y)
            row = rb.get(b.number)
            state = D.bus_state(row["vm_pu"], b.v_min, b.v_max) if row and b.in_service else "ok"
            if st["colour"] and res:
                col = D.bus_colour(state, b.in_service, stale)
            else:
                col = D.COL_GREY if not b.in_service else "#2563eb"
            sel = ("bus", b.number) in self.sel
            if b.number in worst:
                c.create_line(sx0 - 6, sy, sx1 + 6, sy, fill=D.COL_WORST, width=14)
            tag = (f"bus:{b.number}",)
            c.create_line(sx0, sy, sx1, sy, fill="#7c3aed" if sel else col, width=max(4, 6 * self.scale),
                          dash=(6, 4) if not b.in_service else (), tags=tag)
            c.create_text(sx0, sy - 12 * max(self.scale, 0.7), anchor="sw", text=f"{b.name or 'Bus ' + str(b.number)}",
                          fill="#0f172a", font=("Segoe UI", max(7, int(8 * self.scale))), tags=tag)
            self._symbols(b, sx0, sy, st["colour"] and bool(res), col)
            if row and st["boxes"] and b.in_service:
                self._box(sx1 + 6, sy + 8, D.bus_box_text(row, st["kv"]), D.COL_GREY if stale else col)
        if st["colour"] and res:
            self._legend()

    def _symbols(self, b, sx0, sy, coloured, col):
        net = self.model.net
        k = max(self.scale, 0.6)
        gens = [(i, g) for i, g in enumerate(net.generators) if g.bus_number == b.number]
        above = [("ext", None)] if b.kind.upper() == "SLACK" else []
        above += [("gen", (i, g)) for i, g in gens]
        for n, (kind, payload) in enumerate(above):
            x = sx0 + (18 + 34 * n) * self.scale
            if kind == "ext":
                c = D.COL_NORMAL if b.in_service else D.COL_GREY
                self.canvas.create_line(x, sy, x, sy - 28 * k, fill=c, tags=(f"bus:{b.number}",))
                self.canvas.create_rectangle(x - 11 * k, sy - 50 * k, x + 11 * k, sy - 28 * k, outline=c, width=2,
                                             tags=(f"bus:{b.number}",))
                self.canvas.create_text(x, sy - 39 * k, text="EG", font=("Segoe UI", max(6, int(7 * k)), "bold"), fill=c,
                                        tags=(f"bus:{b.number}",))
            else:
                i, g = payload
                c = D.COL_GREY if not g.in_service else D.COL_NORMAL
                tg = (f"gen:{i}",)
                self.canvas.create_line(x, sy, x, sy - 28 * k, fill=c, tags=tg)
                self.canvas.create_oval(x - 11 * k, sy - 50 * k, x + 11 * k, sy - 28 * k, outline="#7c3aed" if ("gen", i) in self.sel else c,
                                        width=2, tags=tg, dash=(4, 3) if not g.in_service else ())
                self.canvas.create_text(x, sy - 39 * k, text="G", font=("Segoe UI", max(6, int(7 * k)), "bold"), fill=c, tags=tg)
        below = []
        if b.p_load or b.q_load:
            below.append("load")
        if b.g_shunt or b.b_shunt:
            below.append("shunt")
        for n, kind in enumerate(below):
            x = sx0 + (18 + 34 * n) * self.scale
            c = D.COL_NORMAL if b.in_service else D.COL_GREY
            tg = (f"bus:{b.number}",)
            self.canvas.create_line(x, sy, x, sy + 18 * k, fill=c, tags=tg)
            if kind == "load":
                self.canvas.create_polygon(x - 8 * k, sy + 18 * k, x + 8 * k, sy + 18 * k, x, sy + 32 * k, outline=c, fill="", width=2, tags=tg)
            else:
                self.canvas.create_line(x - 9 * k, sy + 18 * k, x + 9 * k, sy + 18 * k, fill=c, width=2, tags=tg)
                self.canvas.create_line(x - 9 * k, sy + 23 * k, x + 9 * k, sy + 23 * k, fill=c, width=2, tags=tg)

    def _box(self, x, y, text, colour):
        t = self.canvas.create_text(x, y, anchor="nw", text=text, fill=colour, font=("Segoe UI", 8))
        bb = self.canvas.bbox(t)
        if bb:
            r = self.canvas.create_rectangle(bb[0] - 2, bb[1] - 1, bb[2] + 2, bb[3] + 1, fill="white", outline="#d1d5db")
            self.canvas.tag_lower(r, t)

    def _legend(self):
        h = self.canvas.winfo_height()
        y = h - 14 * (len(D.LEGEND) + 1) - 6
        self.canvas.create_text(10, y, anchor="nw", text="Legend", font=("Segoe UI", 8, "bold"))
        for n, (col, text) in enumerate(D.LEGEND):
            yy = y + 14 * (n + 1)
            self.canvas.create_rectangle(10, yy + 2, 22, yy + 10, fill=col, outline=col)
            self.canvas.create_text(28, yy, anchor="nw", text=text, font=("Segoe UI", 8), fill="#334155")

    def _study_view(self):
        return {"boxes": self.var_boxes.get(), "colour": self.var_colour.get(), "grid": self.var_grid.get(),
                "kv": self.var_kv.get()}

    # ------------------------------------------------------------------ hit testing / selection
    def hit(self, sx, sy):
        ids = self.canvas.find_overlapping(sx - 5, sy - 5, sx + 5, sy + 5)
        found = []
        for i in ids:
            for t in self.canvas.gettags(i):
                if t.startswith(("bus:", "branch:", "gen:")):
                    kind, key = t.split(":")
                    found.append(({"bus": "bus", "branch": "branch", "gen": "gen"}[kind], int(key)))
        for pref in ("gen", "bus", "branch"):
            for k in reversed(found):
                if k[0] == pref:
                    return k
        return None

    def on_press(self, e):
        self.canvas.focus_set()
        if self.tool == "Pan":
            self._pan = (e.x, e.y, self.ox, self.oy)
            return
        if self.tool != "Select":
            self._tool_click(e.x, e.y)
            return
        item = self.hit(e.x, e.y)
        ctrl = bool(e.state & 0x4)
        if item is None:
            if not ctrl:
                self.sel.clear()
            self._band = (e.x, e.y)
            self.selection_changed()
            return
        if ctrl:
            self.sel.symmetric_difference_update({item})
        elif item not in self.sel:
            self.sel = {item}
        self.selection_changed()
        buses = [k for kind, k in self.sel if kind == "bus"]
        if buses:
            wx, wy = self.s2w(e.x, e.y)
            self._drag = {"before": self.model.snapshot(), "start": (wx, wy), "moved": False,
                          "orig": {n: self.model.positions[n] for n in buses}}

    def on_motion(self, e):
        if self.tool == "Pan" and self._pan:
            self.on_pan_move(e)
        elif self._drag:
            wx, wy = self.s2w(e.x, e.y)
            dx, dy = wx - self._drag["start"][0], wy - self._drag["start"][1]
            if abs(dx) + abs(dy) > 1:
                self._drag["moved"] = True
            for n, (x, y) in self._drag["orig"].items():
                self.model.positions[n] = (x + dx, y + dy)
            self.redraw()
        elif self._band:
            self.canvas.delete("band")
            self.canvas.create_rectangle(self._band[0], self._band[1], e.x, e.y, outline="#2563eb", dash=(3, 3), tags="band")

    def on_release(self, e):
        if self._drag:
            d, self._drag = self._drag, None
            if d["moved"]:
                self.model.move_buses({n: self.model.positions[n] for n in d["orig"]}, d["before"])
                self.model_changed()
            else:
                self.model.positions.update(d["orig"])
        elif self._band:
            x0, y0 = self._band
            self._band = None
            self.canvas.delete("band")
            lo, hi = (min(x0, e.x), min(y0, e.y)), (max(x0, e.x), max(y0, e.y))
            for n, (x, y) in self.model.positions.items():
                sx, sy = self.w2s(x, y)
                if lo[0] <= sx <= hi[0] and lo[1] <= sy <= hi[1]:
                    self.sel.add(("bus", n))
            self.selection_changed()
        if self.tool != "Pan":
            self._pan = None

    def on_double(self, e):
        item = self.hit(e.x, e.y)
        if item and self.tool == "Select":
            self.sel = {item}
            self.selection_changed()
            self.open_dialog(*item)

    def on_context(self, e):
        item = self.hit(e.x, e.y)
        if not item:
            return
        if item not in self.sel:
            self.sel = {item}
            self.selection_changed()
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="Properties...", command=lambda: self.open_dialog(*item))
        menu.add_command(label="Delete", command=self.delete_selected)
        el = self._element(*item)
        menu.add_command(label="Put in service" if not el.in_service else "Take out of service",
                         command=lambda: self.set_service(not el.in_service))
        menu.tk_popup(e.x_root, e.y_root)

    def _element(self, kind, key):
        net = self.model.net
        return {"bus": lambda: net.buses[net.index[key]], "branch": lambda: net.branches[key],
                "gen": lambda: net.generators[key]}[kind]()

    def select_from_table(self, kind, key):
        if kind == "branch" or kind == "bus" or kind == "gen":
            self.sel = {(kind, key)}
            self.selection_changed()
            if kind == "bus" and key in self.model.positions:
                x, y = self.model.positions[key]
                sx, sy = self.w2s(x, y)
                w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
                self.ox += w / 2 - sx
                self.oy += h / 2 - sy
            self.redraw()

    def select_all(self):
        self.sel = {("bus", b.number) for b in self.model.net.buses} | {("branch", i) for i in range(len(self.model.net.branches))}
        self.selection_changed()

    def selection_changed(self):
        self.show_properties()
        self.redraw()

    # ------------------------------------------------------------------ tools & palette
    def set_tool(self, tool):
        self.tool = tool
        self.pending_branch = None
        hints = {"Bus": "Click the canvas to place a bus.", "Line": "Click the first bus, then the second bus.",
                 "Transformer": "Click the HV bus, then the LV bus.",
                 "Generator": "Click a bus to attach a generator.", "Load": "Click a bus to add a load.",
                 "Shunt": "Click a bus to add a shunt.", "External grid": "Click a bus to make it the external grid (slack).",
                 "Pan": "Drag to pan.", "Select": "Select / move (Ctrl+click = multi-select)."}
        self.status.set(f"Tool: {tool}. {hints.get(tool, '')}")
        self.canvas.configure(cursor="crosshair" if tool not in ("Select", "Pan") else ("fleur" if tool == "Pan" else ""))

    def cancel_tool(self):
        self.set_tool("Select")

    def _palette_press(self, kind, e):
        self._palette_drag = {"kind": kind, "x": e.x_root, "y": e.y_root, "moved": False}

    def _palette_motion(self, e):
        d = self._palette_drag
        if d and abs(e.x_root - d["x"]) + abs(e.y_root - d["y"]) > 6:
            d["moved"] = True
            self.config(cursor="hand2")

    def _palette_release(self, e):
        d, self._palette_drag = self._palette_drag, None
        self.config(cursor="")
        if not d:
            return
        if not d["moved"]:
            self.set_tool(d["kind"])
            return
        target = self.winfo_containing(e.x_root, e.y_root)
        if target is self.canvas:
            sx, sy = e.x_root - self.canvas.winfo_rootx(), e.y_root - self.canvas.winfo_rooty()
            self.place(d["kind"], sx, sy)
        else:
            self.status.set("Drop the element on the diagram canvas.")

    def place(self, kind, sx, sy):
        """Create ``kind`` at screen position (sx, sy): drag-and-drop or click-to-place."""
        m = self.model
        wx, wy = self.s2w(sx, sy)
        if kind in ("Select", "Pan"):
            self.set_tool(kind)
            return
        if kind == "Bus":
            n = m.add_bus(wx, wy)
            self.sel = {("bus", n)}
            self.set_tool("Select")
        elif kind in ("Line", "Transformer"):
            self.set_tool(kind)
            self.status.set(f"{kind}: click the first bus, then the second bus.")
            bus = self.hit(sx, sy)
            if bus and bus[0] == "bus":
                self._pick_branch_bus(kind, bus[1])
            return
        else:
            bus = self.hit(sx, sy)
            if not bus or bus[0] != "bus":
                self.status.set(f"Drop / click a {kind.lower()} onto a bus.")
                return
            key = m.add_element(kind, bus[1])
            self.sel = {key}
            self.set_tool("Select")
        self.model_changed()

    def _pick_branch_bus(self, kind, number):
        if self.pending_branch is None:
            self.pending_branch = number
            self.status.set(f"First bus {number} selected - click the second bus (Esc cancels).")
            return
        a, self.pending_branch = self.pending_branch, None
        try:
            i = self.model.add_branch(kind, a, number)
        except ValueError as exc:
            messagebox.showwarning("Cannot connect", str(exc))
            return
        self.sel = {("branch", i)}
        self.status.set(f"{kind} {a}-{number} added; edit its data in the Properties panel before running.")
        self.model_changed()

    def _tool_click(self, sx, sy):
        """Canvas click while a palette tool is active."""
        if self.tool in ("Line", "Transformer"):
            h = self.hit(sx, sy)
            if h and h[0] == "bus":
                self._pick_branch_bus(self.tool, h[1])
        else:
            self.place(self.tool, sx, sy)

    # ------------------------------------------------------------------ properties panel
    def show_properties(self):
        for w in self.prop_inner.winfo_children():
            w.destroy()
        self.prop_form = None
        sel = sorted(self.sel, key=str)
        if len(sel) == 0:
            self.prop_title.configure(text="Nothing selected")
            return
        if len(sel) > 1:
            self.prop_title.configure(text=f"{len(sel)} elements selected")
            ttk.Label(self.prop_inner, text="Use Delete, Copy or the right-click menu.").pack(anchor="w")
            return
        kind, key = sel[0]
        try:
            self.prop_form = PropertyForm(self.prop_inner, self.model, kind, key, on_applied=self.after_apply)
        except (KeyError, IndexError):
            self.sel.clear()
            return
        self.prop_form.pack(fill="x")
        name = {"bus": f"Bus {key}", "branch": f"Branch {key}", "gen": f"Generator {key}"}[kind]
        self.prop_title.configure(text=f"Selected: {name}")
        if kind == "bus":
            box = ttk.LabelFrame(self.prop_inner, text="Generators at this bus")
            box.pack(fill="x", pady=6)
            for i, g in enumerate(self.model.net.generators):
                if g.bus_number == key:
                    ttk.Button(box, text=g.name or f"Generator {i}", command=lambda i=i: self._select_gen(i)).pack(fill="x")
            ttk.Button(box, text="Add generator", command=lambda: self._add_gen(key)).pack(fill="x", pady=2)

    def _select_gen(self, i):
        self.sel = {("gen", i)}
        self.selection_changed()

    def _add_gen(self, bus):
        self.sel = {self.model.add_element("Generator", bus)}
        self.model_changed()

    def apply_properties(self):
        if self.prop_form is not None:
            self.prop_form.apply()

    def after_apply(self):
        self.status.set("Properties applied; results (if any) are now stale.")
        self.model_changed(keep_form=True)

    def open_dialog(self, kind, key):
        top = tk.Toplevel(self)
        top.title("Properties")
        top.transient(self)
        form = PropertyForm(top, self.model, kind, key, on_applied=lambda: self.model_changed())
        form.pack(fill="both", expand=True, padx=10, pady=8)
        bar = ttk.Frame(top)
        bar.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(bar, text="Apply", command=form.apply).pack(side="left")
        ttk.Button(bar, text="OK", command=lambda: top.destroy() if form.apply() else None).pack(side="left", padx=4)
        ttk.Button(bar, text="Cancel", command=top.destroy).pack(side="left")

    # ------------------------------------------------------------------ edit commands
    def model_changed(self, fit=False, keep_form=False):
        m = self.model
        self.sel = {k for k in self.sel if self._exists(k)}
        self.update_run_state()
        self.update_title()
        self.update_status()
        self.refresh_tables()
        if fit:
            self.update_idletasks()
            self.fit_view()
        else:
            self.redraw()
        if not keep_form:
            self.show_properties()

    def _exists(self, key):
        kind, k = key
        net = self.model.net
        return (kind == "bus" and k in net.index) or (kind == "branch" and 0 <= k < len(net.branches)) \
            or (kind == "gen" and 0 <= k < len(net.generators))

    def undo(self):
        if self.model.undo():
            self.status.set("Undo (results marked stale).")
            self.model_changed()

    def redo(self):
        if self.model.redo():
            self.status.set("Redo (results marked stale).")
            self.model_changed()

    def delete_selected(self):
        if not self.sel:
            return
        self.model.delete(list(self.sel))
        self.sel.clear()
        self.model_changed()

    def set_service(self, flag):
        if self.sel:
            self.model.set_in_service(list(self.sel), flag)
            self.model_changed()

    def copy_selected(self):
        n = self.model.copy(list(self.sel))
        self.status.set(f"Copied {n} bus(es) with their generators and internal branches." if n else
                        "Select one or more buses to copy.")

    def paste(self):
        sel = self.model.paste()
        if sel:
            self.sel = set(sel)
            self.model_changed()

    def find_element(self):
        text = simpledialog.askstring("Find element", "Name or number:", parent=self)
        if not text:
            return
        hits = self.model.find(text)
        if not hits:
            self.status.set(f"Nothing matches '{text}'.")
            return
        self.sel = set(hits)
        self.selection_changed()
        self.status.set(f"{len(hits)} element(s) found.")
        if hits[0][0] == "bus":
            self.select_from_table(*hits[0])
            self.sel = set(hits)
            self.redraw()

    def auto_layout(self):
        self.model.auto_layout()
        self.model_changed(fit=True)
        self.status.set("Force-directed layout applied (drag buses to rearrange; Save keeps the layout).")

    def set_base(self):
        base = simpledialog.askfloat("System base", "Base power (MVA):", initialvalue=self.model.net.base_mva,
                                     minvalue=0.001, parent=self)
        if base is None:
            return
        freq = simpledialog.askfloat("System frequency", "Frequency (Hz):", initialvalue=self.model.net.frequency_hz,
                                     minvalue=1, parent=self)
        if freq is None:
            return
        self.model.set_base(base, freq)
        self.model_changed()

    # ------------------------------------------------------------------ study / run
    def _sync_study_to_widgets(self):
        s = self.model.study
        self.var_tol.set(f"{s.tolerance:g}")
        self.var_iter.set(str(s.max_iterations))
        self.var_start.set({"flat": "Flat start", "previous": "Previous solution", "data": "Case data"}[s.start])
        self.var_qlim.set(s.enforce_q_limits)
        self.var_scale.set(f"{s.load_scale:g}")
        self.var_boxes.set(s.show_boxes)
        self.var_colour.set(s.show_colours)
        self.var_grid.set(s.show_grid)
        self.var_kv.set(s.show_kv)

    def commit_study(self):
        ok = True
        for ent, var, name, lo, hi in ((self.ent_tol, self.var_tol, "tolerance", 0.0, 1.0),
                                       (self.ent_scale, self.var_scale, "load scaling", 0.0, 100.0)):
            try:
                v = float(var.get())
                if not (lo < v <= hi if name == "load scaling" else lo < v < hi):
                    raise ValueError
                ent.configure(bg="white")
            except ValueError:
                ent.configure(bg="#fee2e2")
                ok = False
        try:
            it = int(float(self.var_iter.get()))
            if it < 1:
                raise ValueError
            self.ent_iter.configure(bg="white")
        except ValueError:
            self.ent_iter.configure(bg="#fee2e2")
            ok = False
        if not ok:
            self.status.set("Invalid study settings (highlighted): tolerance in (0,1), iterations >= 1, scaling in (0,100].")
            return False
        s = self.model.study
        s.tolerance, s.max_iterations = float(self.var_tol.get()), int(float(self.var_iter.get()))
        s.load_scale, s.enforce_q_limits = float(self.var_scale.get()), self.var_qlim.get()
        s.start = {"Flat start": "flat", "Previous solution": "previous", "Case data": "data"}[self.var_start.get()]
        s.show_boxes, s.show_colours = self.var_boxes.get(), self.var_colour.get()
        s.show_grid, s.show_kv = self.var_grid.get(), self.var_kv.get()
        return True

    def _commit_study_quiet(self):
        self.commit_study()

    def focus_study(self):
        self.ent_tol.focus_set()

    def ask_scaling(self):
        v = simpledialog.askfloat("Global load scaling", "Multiply all loads by:", initialvalue=float(self.var_scale.get() or 1),
                                  minvalue=0.01, maxvalue=100.0, parent=self)
        if v is not None:
            self.var_scale.set(f"{v:g}")
            self.commit_study()

    def update_run_state(self):
        errs = self.model.errors()
        state = "disabled" if errs else "normal"
        self.run_button.configure(state=state)
        if "Run NR (F5)" in self.toolbar_buttons:
            self.toolbar_buttons["Run NR (F5)"].configure(state=state)
        self.study_menu.entryconfig(RUN_LABEL, state=state)
        self.run_problems = errs

    def check_network(self):
        issues = self.model.issues()
        if not issues:
            messagebox.showinfo("Check network", "No problems found.")
            self.status.set("Network check passed.")
        else:
            messagebox.showwarning("Check network", "\n".join(str(i) for i in issues[:40]))
            self.status.set(f"Network check: {sum(i.level == 'error' for i in issues)} error(s), "
                            f"{sum(i.level == 'warning' for i in issues)} warning(s).")

    def run_nr(self):
        if not self.commit_study():
            return
        errs = self.model.errors()
        if errs:
            messagebox.showwarning("Cannot run", "Fix the network first:\n" + "\n".join(e.message for e in errs[:10]))
            return
        self.log.delete("1.0", "end")
        self.nb.select(self.log_tab)
        count = [0]

        def cb(line):
            self.log.insert("end", line + "\n")
            self.log.see("end")
            count[0] += 1
            if count[0] % 4 == 0:
                self.update_idletasks()

        self.status.set("Solving...")
        self.update_idletasks()
        try:
            res = self.model.run(cb)
        except Exception as exc:
            self.status.set(f"Load flow failed: {exc}")
            messagebox.showerror("Load flow failed", f"{type(exc).__name__}: {exc}")
            return
        self.plot.plot(res, self.model.study.tolerance)
        self.model_changed(keep_form=True)
        if res["converged"]:
            self.nb.select(self.tables["Bus results"])
        else:
            worst = ", ".join(str(b) for b in res["worst_buses"][:3])
            messagebox.showwarning("Load flow DIVERGED",
                                   f"{res['message']}\nLast maximum mismatch: {res['max_mismatch_pu']:.3e} pu\n"
                                   f"Buses with the largest mismatch (orange on the diagram): {worst}")
            self.nb.select(self.plot_tab)

    # ------------------------------------------------------------------ results
    def refresh_tables(self):
        m = self.model
        res = m.results
        if res is None:
            for t in self.tables.values():
                t.clear()
            return
        if not m.stale:
            net = m.net
            h, r, k = report.bus_table(net, res)
            self.tables["Bus results"].set_data(h, r, k)
            h, r, k = report.branch_table(net, res)
            tags = [D.loading_state(x["loading_percent"]) if x["active"] else "" for x in res["branch_results"]]
            self.tables["Branch results"].set_data(h, r, k, tags)
            h, r, k = report.gen_table(net, res)
            self.tables["Generators"].set_data(h, r, k)
            h, r, k = report.violation_table(net, res)
            self.tables["Violations"].set_data(h, r, k)
        for t in self.tables.values():
            t.set_stale(m.stale)

    def update_status(self):
        m = self.model
        net = m.net
        base = f"base {net.base_mva:g} MVA, {net.frequency_hz:g} Hz"
        r = m.results
        if r is None:
            self.status.set(f"Status: not run  |  {base}")
            return
        self.status.set(f"Status: {'converged' if r['converged'] else 'DIVERGED'}  |  iterations {r['iterations']}  |  "
                        f"max mismatch {r['max_mismatch_pu']:.2e} pu  |  solve time {r['solve_time_s'] * 1e3:.2f} ms  |  {base}"
                        f"{'  |  STALE - edited since last run' if m.stale else ''}")

    def show_tab(self, name):
        self.nb.select(self.tables[name])

    def show_loss_summary(self):
        if self.model.results is None:
            messagebox.showinfo("Loss summary", "Run a load flow first.")
            return
        messagebox.showinfo("Loss summary", "\n".join(f"{a}: {b}" for a, b in report.summary_rows(self.model.net, self.model.results)))

    # ------------------------------------------------------------------ files
    def update_title(self):
        name = self.model.path.name if self.model.path else "untitled"
        self.title(f"{APP_TITLE} \u2014 {name}{' *' if self.model.dirty else ''}")

    def _confirm_discard(self):
        if not self.model.dirty:
            return True
        ans = messagebox.askyesnocancel("Unsaved changes", "Save changes to the current project?")
        if ans is None:
            return False
        if ans:
            return self.file_save()
        return True

    def _load_model(self, model):
        self.model = model
        self.sel.clear()
        self.plot.clear()
        self.log.delete("1.0", "end")
        self._sync_study_to_widgets()
        self.model_changed(fit=True)

    def file_new(self):
        if self._confirm_discard():
            self._load_model(ProjectModel())

    def open_example(self):
        if self._confirm_discard():
            self._load_model(ProjectModel.import_case(DEFAULT_CASE))

    def file_open(self, path=None):
        if not self._confirm_discard():
            return
        path = path or filedialog.askopenfilename(filetypes=[("Load-flow project", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            self._load_model(ProjectModel.open(path))
            self.add_recent(path)
        except Exception as exc:
            messagebox.showerror("Open failed", f"{type(exc).__name__}: {exc}")

    def file_save(self):
        if not self.model.path:
            return self.file_save_as()
        return self._save(self.model.path)

    def file_save_as(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("Load-flow project", "*.json")],
                                            initialfile="project.json")
        return self._save(path) if path else False

    def _save(self, path):
        try:
            self.commit_study()
            self.model.save(path)
            self.add_recent(str(path))
            self.update_title()
            self.status.set(f"Saved {path}")
            return True
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return False

    def file_import(self):
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(title="Import case", initialdir=str(Path(__file__).parent / "data"),
                                          filetypes=[("MATPOWER / IEEE CDF", "*.m *.cdf *.txt *.dat"), ("All files", "*.*")])
        if not path:
            return
        try:
            self._load_model(ProjectModel.import_case(path))
            self.status.set(f"Imported {Path(path).name} with automatic layout; drag buses to rearrange, then save.")
        except Exception as exc:
            messagebox.showerror("Import failed", f"{type(exc).__name__}: {exc}")

    def export_matpower(self):
        path = filedialog.asksaveasfilename(defaultextension=".m", filetypes=[("MATPOWER case", "*.m")], initialfile="case_export.m")
        if path:
            try:
                save_matpower_case(self.model.net, path)
                self.status.set(f"Exported {path}")
            except Exception as exc:
                messagebox.showerror("Export failed", str(exc))

    def _need_results(self):
        if self.model.results is None:
            messagebox.showwarning("No results", "Run a load flow first.")
            return False
        if self.model.stale and not messagebox.askyesno("Stale results", "The network changed since the last run. Export the old results anyway?"):
            return False
        return True

    def export_results(self):
        if not self._need_results():
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", initialfile="results.xlsx",
                                            filetypes=[("Excel workbook", "*.xlsx"), ("CSV files (folder)", "*.csv")])
        if not path:
            return
        try:
            m = self.model
            if path.lower().endswith(".csv"):
                folder = Path(path).with_suffix("")
                report.export_csv(folder, m.net, m.results)
                self.status.set(f"CSV files written to {folder}")
            else:
                report.export_xlsx(path, m.net, m.results)
                self.status.set(f"Excel results written to {path}")
        except Exception as exc:
            messagebox.showerror("Export failed", f"{type(exc).__name__}: {exc}")

    def export_report(self):
        if not self._need_results():
            return
        path = filedialog.asksaveasfilename(defaultextension=".pdf", initialfile="load_flow_report.pdf",
                                            filetypes=[("PDF report", "*.pdf"), ("HTML report", "*.html")])
        if not path:
            return
        m, st = self.model, self._study_view()
        try:
            fn = report.export_html if path.lower().endswith(".html") else report.export_pdf
            fn(path, m.net, m.results, m.positions, m.stale, st["kv"])
            self.status.set(f"Report written to {path}")
        except Exception as exc:
            messagebox.showerror("Report failed", f"{type(exc).__name__}: {exc}")

    # ---- recent files
    def _recent(self):
        try:
            return json.loads(RECENT_FILE.read_text())
        except Exception:
            return []

    def add_recent(self, path):
        rec = [p for p in self._recent() if p != str(path)]
        try:
            RECENT_FILE.write_text(json.dumps(([str(path)] + rec)[:8]))
        except OSError:
            pass
        self.update_recent_menu()

    def update_recent_menu(self):
        self.recent_menu.delete(0, "end")
        for p in self._recent():
            self.recent_menu.add_command(label=p, command=lambda p=p: self.file_open(p))

    # ------------------------------------------------------------------ misc
    def reset_layout(self):
        self.update_idletasks()
        try:
            self.vpane.sashpos(0, int(self.winfo_height() * 0.62))
            self.hpane.sashpos(0, 175)
            self.hpane.sashpos(1, max(self.winfo_width() - 310, 400))
        except tk.TclError:
            pass

    def user_guide(self):
        top = tk.Toplevel(self)
        top.title("User guide")
        txt = tk.Text(top, wrap="word", width=96, height=30, font=("Segoe UI", 10))
        txt.insert("1.0", USER_GUIDE)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=8, pady=8)

    def about(self):
        messagebox.showinfo("About", f"{about_info.COURSE}\nNewton-Raphson Load Flow Simulator\n"
                                     f"Version {about_info.VERSION}\n\nStudent: {about_info.STUDENT_NAME}\n"
                                     f"Roll number: {about_info.ROLL_NUMBER}")

    def on_close(self):
        if self._confirm_discard():
            self.destroy()


if __name__ == "__main__":
    app = LoadFlowApp()
    app.update_recent_menu()
    app.mainloop()
