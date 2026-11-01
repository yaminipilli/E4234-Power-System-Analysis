"""Tkinter desktop GUI for the E4234 AC load-flow simulator.

The GUI uses the project's own Network model and Newton-Raphson solver.
Run with: python gui_app.py
"""
from __future__ import annotations

import csv
import json
import math
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from dataclasses import asdict
from pathlib import Path

from network import Bus, Branch, Generator, Network
from solver import solve_power_flow, PowerFlowError
from matpower_import import load_matpower_case


APP_TITLE = "E4 234 Load Flow Simulator"
DEFAULT_CASE = Path(__file__).parent / "data" / "case14.m"


def network_to_dict(net):
    return {
        "base_mva": net.base_mva,
        "frequency_hz": net.frequency_hz,
        "metadata": net.metadata,
        "buses": [asdict(x) for x in net.buses],
        "branches": [asdict(x) for x in net.branches],
        "generators": [asdict(x) for x in net.generators],
    }


def network_from_dict(d):
    return Network(
        buses=[Bus(**x) for x in d["buses"]],
        branches=[Branch(**x) for x in d.get("branches", [])],
        generators=[Generator(**x) for x in d.get("generators", [])],
        base_mva=float(d.get("base_mva", 100)),
        frequency_hz=float(d.get("frequency_hz", 50)),
        metadata=d.get("metadata", {}),
    )


class LoadFlowApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE + " — Untitled project")
        self.geometry("1400x850")
        self.minsize(1000, 650)
        self.net = None
        self.results = None
        self.project_path = None
        self.selected_bus = None
        self.pending_branch = None
        self.tool = "Select"
        self.bus_positions = {}
        self.undo_stack = []
        self.redo_stack = []
        self.show_result_boxes = tk.BooleanVar(value=True)
        self.colour_coding = tk.BooleanVar(value=True)
        self.flat_start = tk.BooleanVar(value=False)
        self.enforce_q = tk.BooleanVar(value=False)
        self.tolerance = tk.StringVar(value="1e-8")
        self.max_iterations = tk.IntVar(value=30)
        self.load_scale = tk.DoubleVar(value=1.0)
        self.base_mva_var = tk.DoubleVar(value=100.0)
        self._build_ui()
        self.new_project()

    def _build_ui(self):
        self._build_menus()
        toolbar = ttk.Frame(self, padding=(6, 4))
        toolbar.pack(side="top", fill="x")
        for label, command in [
            ("New", self.new_project), ("Open", self.open_project),
            ("Save", self.save_project), ("Import MATPOWER", self.import_case),
            ("Undo", self.undo), ("Redo", self.redo),
            ("Check network", self.check_network), ("Run NR (F5)", self.run_nr),
            ("Show results", self.refresh_results),
        ]:
            ttk.Button(toolbar, text=label, command=command).pack(side="left", padx=2)
        ttk.Label(toolbar, text="  Base MVA:").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.base_mva_var, width=7).pack(side="left")
        ttk.Button(toolbar, text="Apply base", command=self.apply_base).pack(side="left", padx=2)

        main = ttk.Panedwindow(self, orient="horizontal")
        main.pack(fill="both", expand=True, padx=5, pady=4)
        left = ttk.Frame(main, width=150, padding=5)
        center = ttk.Frame(main, padding=3)
        right = ttk.Frame(main, width=250, padding=5)
        main.add(left, weight=0); main.add(center, weight=4); main.add(right, weight=1)

        ttk.Label(left, text="ELEMENT PALETTE", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))
        ttk.Label(left, text="Choose an element, then click the canvas.", wraplength=135).pack(anchor="w", pady=(0, 8))
        for item in ["Select", "Bus", "Line", "Transformer", "Generator", "Load", "Shunt", "External grid"]:
            ttk.Button(left, text=item, command=lambda x=item: self.set_tool(x)).pack(fill="x", pady=2)
        ttk.Separator(left).pack(fill="x", pady=8)
        ttk.Label(left, text="Study case", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(left, text="Tolerance").pack(anchor="w")
        ttk.Entry(left, textvariable=self.tolerance, width=12).pack(anchor="w")
        ttk.Label(left, text="Maximum iterations").pack(anchor="w", pady=(5, 0))
        ttk.Spinbox(left, from_=1, to=200, textvariable=self.max_iterations, width=8).pack(anchor="w")
        ttk.Checkbutton(left, text="Flat start", variable=self.flat_start).pack(anchor="w", pady=(5, 0))
        ttk.Checkbutton(left, text="Enforce Q limits", variable=self.enforce_q).pack(anchor="w")
        ttk.Label(left, text="Global load scale").pack(anchor="w", pady=(5, 0))
        ttk.Scale(left, from_=0.1, to=2.0, variable=self.load_scale, orient="horizontal").pack(fill="x")
        ttk.Label(left, textvariable=self.load_scale).pack(anchor="e")

        ttk.Label(center, text="SINGLE-LINE DIAGRAM", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.canvas = tk.Canvas(center, background="white", highlightthickness=1, highlightbackground="#cbd5e1")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self.canvas_click)
        self.canvas.bind("<B1-Motion>", self.drag_selected)
        self.canvas.bind("<MouseWheel>", self.zoom_canvas)
        self.canvas.bind("<Button-3>", self.canvas_context)
        self.canvas.bind("<Configure>", lambda _e: self.draw_network())

        ttk.Label(right, text="PROPERTIES", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))
        self.property_title = ttk.Label(right, text="Nothing selected")
        self.property_title.pack(anchor="w")
        self.property_fields = {}
        form = ttk.Frame(right)
        form.pack(fill="x", pady=5)
        fields = [
            ("Name", "name"), ("Bus number", "number"), ("Type", "kind"),
            ("V (pu)", "vm"), ("Angle (deg)", "va_deg"),
            ("P load (MW)", "p_load_mw"), ("Q load (MVAr)", "q_load_mvar"),
            ("P gen (MW)", "p_gen_mw"), ("Q gen (MVAr)", "q_gen_mvar"),
            ("R (pu)", "r"), ("X (pu)", "x"), ("B (pu)", "b"),
            ("Tap", "tap"), ("Shift (deg)", "shift_deg"),
            ("Rating (MVA)", "rate_mva"), ("In service", "in_service"),
        ]
        for row, (label, key) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=2)
            var = tk.StringVar()
            self.property_fields[key] = var
            ttk.Entry(form, textvariable=var, width=14).grid(row=row, column=1, sticky="ew", pady=2)
        form.columnconfigure(1, weight=1)
        ttk.Button(right, text="Apply properties", command=self.apply_properties).pack(fill="x", pady=4)
        ttk.Button(right, text="Delete selected", command=self.delete_selected).pack(fill="x", pady=2)
        ttk.Label(right, text="Tip: click two buses with Line/Transformer selected to connect them.",
                  wraplength=230).pack(anchor="w", pady=8)

        self._build_results()
        self.status = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w", padding=4).pack(side="bottom", fill="x")
        self.bind("<F5>", lambda _e: self.run_nr())
        self.bind("<Control-n>", lambda _e: self.new_project())
        self.bind("<Control-o>", lambda _e: self.open_project())
        self.bind("<Control-s>", lambda _e: self.save_project())
        self.bind("<Control-k>", lambda _e: self.check_network())
        self.bind("<Control-z>", lambda _e: self.undo())
        self.bind("<Control-y>", lambda _e: self.redo())
        self.bind("<Delete>", lambda _e: self.delete_selected())

    def _build_menus(self):
        bar = tk.Menu(self)
        file_menu = tk.Menu(bar, tearoff=False)
        for label, command, shortcut in [
            ("New", self.new_project, "Ctrl+N"), ("Open project", self.open_project, "Ctrl+O"),
            ("Save project", self.save_project, "Ctrl+S"), ("Save as", self.save_as, ""),
            ("Import MATPOWER", self.import_case, ""), ("Export results CSV", self.export_csv, ""),
            ("Export report HTML", self.export_html, ""), ("Exit", self.destroy, ""),
        ]:
            file_menu.add_command(label=f"{label}\t{shortcut}" if shortcut else label, command=command)
        bar.add_cascade(label="File", menu=file_menu)
        edit = tk.Menu(bar, tearoff=False)
        for label, command in [("Undo", self.undo), ("Redo", self.redo), ("Delete", self.delete_selected)]:
            edit.add_command(label=label, command=command)
        bar.add_cascade(label="Edit", menu=edit)
        view = tk.Menu(bar, tearoff=False)
        view.add_checkbutton(label="Show result boxes", variable=self.show_result_boxes, command=self.draw_network)
        view.add_checkbutton(label="Colour coding", variable=self.colour_coding, command=self.draw_network)
        view.add_command(label="Fit to window", command=self.draw_network)
        bar.add_cascade(label="View", menu=view)
        network = tk.Menu(bar, tearoff=False)
        network.add_command(label="Check network\tCtrl+K", command=self.check_network)
        network.add_command(label="Auto-layout", command=self.auto_layout)
        network.add_command(label="System base MVA", command=self.apply_base)
        bar.add_cascade(label="Network", menu=network)
        study = tk.Menu(bar, tearoff=False)
        study.add_command(label="Load flow settings", command=self.show_settings)
        study.add_command(label="Run Newton-Raphson\tF5", command=self.run_nr)
        bar.add_cascade(label="Study", menu=study)
        results = tk.Menu(bar, tearoff=False)
        results.add_command(label="Show results", command=self.refresh_results)
        results.add_command(label="Export CSV", command=self.export_csv)
        results.add_command(label="Export HTML report", command=self.export_html)
        bar.add_cascade(label="Results", menu=results)
        help_menu = tk.Menu(bar, tearoff=False)
        help_menu.add_command(label="User guide", command=self.user_guide)
        help_menu.add_command(label="About", command=lambda: messagebox.showinfo("About", "E4 234 Load Flow Simulator\nPython + SciPy sparse Newton–Raphson"))
        bar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=bar)

    def _build_results(self):
        outer = ttk.Frame(self, padding=(5, 2))
        outer.pack(side="bottom", fill="both", expand=False)
        self.result_notebook = ttk.Notebook(outer)
        self.result_notebook.pack(fill="both", expand=True)
        self.result_trees = {}
        specs = {
            "Bus results": ["Bus", "Type", "V (pu)", "V (kV)", "Angle (deg)", "P inj (MW)", "Q inj (MVAr)", "Limit"],
            "Branch results": ["From", "To", "P from", "Q from", "P to", "Q to", "Loading %", "P loss MW", "Q loss MVAr"],
            "Generators": ["Bus", "Generator", "Pg MW", "Qg MVAr", "In service"],
            "Violations": ["Element", "Violation", "Value"],
            "Iteration log": ["Iteration", "Maximum mismatch (pu)"],
        }
        for tab, cols in specs.items():
            frame = ttk.Frame(self.result_notebook)
            tree = ttk.Treeview(frame, columns=cols, show="headings", height=5)
            for col in cols:
                tree.heading(col, text=col)
                tree.column(col, width=100, minwidth=65, stretch=True, anchor="center")
            yscroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            xscroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
            tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
            tree.grid(row=0, column=0, sticky="nsew")
            yscroll.grid(row=0, column=1, sticky="ns")
            xscroll.grid(row=1, column=0, sticky="ew")
            frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
            self.result_notebook.add(frame, text=tab)
            self.result_trees[tab] = tree

    def new_project(self):
        if self.net is not None and not messagebox.askyesno("New project", "Discard current project and create a new network?"):
            return
        self.net = Network(buses=[Bus(number=1, kind="SLACK", name="Bus 1")], branches=[], base_mva=100.0)
        self.results = None
        self.project_path = None
        self.bus_positions = {1: (260, 180)}
        self.selected_bus = None
        self.title(APP_TITLE + " — Untitled project")
        self._mark_stale()
        self.draw_network()
        self.status.set("New project. Add buses, then connect branches and specify loads/generators.")

    def _snapshot(self):
        if self.net:
            self.undo_stack.append(json.dumps(network_to_dict(self.net)))
            self.undo_stack = self.undo_stack[-20:]
            self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(json.dumps(network_to_dict(self.net)))
        d = json.loads(self.undo_stack.pop())
        self.net = network_from_dict(d)
        self._restore_positions()
        self.results = None
        self.draw_network()
        self.refresh_results()
        self.status.set("Undo applied; previous results marked stale.")

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(json.dumps(network_to_dict(self.net)))
        self.net = network_from_dict(json.loads(self.redo_stack.pop()))
        self._restore_positions()
        self.results = None
        self.draw_network()
        self.refresh_results()
        self.status.set("Redo applied; previous results marked stale.")

    def _restore_positions(self):
        w = max(self.canvas.winfo_width(), 600)
        h = max(self.canvas.winfo_height(), 350)
        self.bus_positions = {}
        n = len(self.net.buses)
        for k, b in enumerate(self.net.buses):
            a = 2 * math.pi * k / max(n, 1)
            self.bus_positions[b.number] = (w/2 + min(w,h)*0.34*math.cos(a), h/2 + min(w,h)*0.34*math.sin(a))

    def set_tool(self, tool):
        self.tool = tool
        self.pending_branch = None
        self.status.set(f"Tool: {tool}. " + ("Click canvas to add a bus." if tool == "Bus" else
                         "Click two buses to connect." if tool in ("Line", "Transformer") else
                         "Click a bus to attach this element." if tool in ("Generator", "Load", "Shunt", "External grid") else
                         "Select or move elements."))

    def canvas_click(self, event):
        x, y = event.x, event.y
        bus = self._bus_at(x, y)
        if self.tool == "Bus":
            self._snapshot()
            number = max((b.number for b in self.net.buses), default=0) + 1
            self.net.buses.append(Bus(number=number, name=f"Bus {number}"))
            self.bus_positions[number] = (x, y)
            self.results = None
            self.selected_bus = number
            self.tool = "Select"
            self._show_properties("bus", number)
            self.draw_network()
            return
        if self.tool in ("Line", "Transformer"):
            if bus is None:
                self.status.set("Select a bus to connect the branch.")
                return
            if self.pending_branch is None:
                self.pending_branch = bus
                self.status.set(f"First bus {bus} selected; click the second bus.")
            else:
                if self.pending_branch == bus:
                    self.status.set("A branch must connect two different buses.")
                    return
                self._snapshot()
                cls = self.tool
                # Transformer tap/shift can be edited in properties after creation.
                self.net.branches.append(Branch(from_bus=self.pending_branch, to_bus=bus,
                                                r=0.01, x=0.1, b=0.0, tap=1.0,
                                                name=f"{cls} {self.pending_branch}-{bus}"))
                if cls == "Transformer":
                    self.net.branches[-1].name = f"Transformer {self.pending_branch}-{bus}"
                self.pending_branch = None
                self.tool = "Select"
                self.results = None
                self.draw_network()
                self.status.set("Branch added with placeholder impedance; edit its properties before running.")
            return
        if self.tool in ("Generator", "Load", "Shunt", "External grid"):
            if bus is None:
                self.status.set("Click a bus to attach the element.")
                return
            self.selected_bus = bus
            self._snapshot()
            b = self.net.buses[self.net.index[bus]]
            if self.tool == "External grid":
                for old in self.net.buses:
                    if old.kind.upper() == "SLACK": old.kind = "PQ"
                b.kind = "SLACK"; b.vm = 1.0; b.va_deg = 0.0
                self.status.set(f"Bus {bus} set as slack/external grid.")
            elif self.tool == "Generator":
                if b.kind.upper() == "PQ": b.kind = "PV"
                self.net.generators.append(Generator(bus_number=bus, p_gen=0.0, q_gen=0.0, name=f"Generator at bus {bus}"))
                self.status.set(f"Generator attached to bus {bus}; edit P/Q/V fields.")
            elif self.tool == "Load":
                b.p_load += 0.1; b.q_load += 0.05
                self.status.set(f"Load added to bus {bus}; edit load properties.")
            elif self.tool == "Shunt":
                b.g_shunt += 0.0; b.b_shunt += 0.01
                self.status.set(f"Shunt susceptance added to bus {bus}; edit bus properties.")
            self.results = None
            self.tool = "Select"
            self._show_properties("bus", bus)
            self.draw_network()
            return
        if bus is not None:
            self.selected_bus = bus
            self._show_properties("bus", bus)
            self.draw_network()
        else:
            self.selected_bus = None
            self.property_title.configure(text="Nothing selected")
            self.draw_network()

    def _bus_at(self, x, y):
        for number, (bx, by) in self.bus_positions.items():
            if (x-bx)**2 + (y-by)**2 < 24**2:
                return number
        return None

    def drag_selected(self, event):
        if self.tool == "Select" and self.selected_bus is not None and self._bus_at(event.x, event.y) == self.selected_bus:
            self.bus_positions[self.selected_bus] = (event.x, event.y)
            self.draw_network()

    def zoom_canvas(self, event):
        # Canvas uses logical coordinates; redraw with a modest scale factor.
        factor = 1.1 if event.delta > 0 else 0.9
        self.bus_positions = {k: (max(40, x*factor), max(40, y*factor)) for k, (x,y) in self.bus_positions.items()}
        self.draw_network()

    def canvas_context(self, event):
        bus = self._bus_at(event.x, event.y)
        if bus is not None:
            self.selected_bus = bus
            self._show_properties("bus", bus)
            menu = tk.Menu(self, tearoff=False)
            menu.add_command(label="Properties", command=lambda: self._show_properties("bus", bus))
            menu.add_command(label="Delete bus", command=self.delete_selected)
            menu.add_command(label="Take out of service", command=lambda: self.toggle_service(bus))
            menu.tk_popup(event.x_root, event.y_root)

    def toggle_service(self, number):
        b = self.net.buses[self.net.index[number]]
        self._snapshot(); b.in_service = not b.in_service
        self.results = None; self.draw_network()
        self.status.set(f"Bus {number} in_service={b.in_service}; results are stale.")

    def _show_properties(self, kind, key):
        self.property_title.configure(text=f"Selected {kind}: {key}")
        for var in self.property_fields.values(): var.set("")
        if kind != "bus": return
        b = self.net.buses[self.net.index[key]]
        vals = {
            "name": b.name, "number": b.number, "kind": b.kind,
            "vm": b.vm, "va_deg": b.va_deg, "p_load_mw": b.p_load*self.net.base_mva,
            "q_load_mvar": b.q_load*self.net.base_mva, "p_gen_mw": b.p_gen*self.net.base_mva,
            "q_gen_mvar": b.q_gen*self.net.base_mva, "in_service": b.in_service,
        }
        for k, v in vals.items():
            if k in self.property_fields: self.property_fields[k].set(str(v))

    def apply_properties(self):
        if self.selected_bus is None: return
        try:
            b = self.net.buses[self.net.index[self.selected_bus]]
            d = {k: v.get().strip() for k,v in self.property_fields.items()}
            self._snapshot()
            if d["name"]: b.name = d["name"]
            if d["kind"]:
                kind = d["kind"].upper()
                if kind not in ("SLACK", "PV", "PQ"): raise ValueError("Type must be SLACK, PV or PQ")
                if kind == "SLACK":
                    for other in self.net.buses:
                        if other is not b and other.kind.upper() == "SLACK": other.kind = "PQ"
                b.kind = kind
            for key, attr in [("vm","vm"), ("va_deg","va_deg")]:
                if d[key]: setattr(b, attr, float(d[key]))
            for key, attr in [("p_load_mw","p_load"), ("q_load_mvar","q_load"), ("p_gen_mw","p_gen"), ("q_gen_mvar","q_gen")]:
                if d[key]: setattr(b, attr, float(d[key])/self.net.base_mva)
            if d["in_service"]: b.in_service = d["in_service"].lower() not in ("false","0","no","off")
            if b.vm <= 0: raise ValueError("Voltage magnitude must be positive")
            self.results = None
            self.draw_network()
            self.status.set("Properties applied; previous results are stale.")
        except Exception as exc:
            messagebox.showerror("Invalid properties", str(exc))

    def delete_selected(self):
        if self.selected_bus is None: return
        number = self.selected_bus
        if len(self.net.buses) <= 1:
            messagebox.showwarning("Cannot delete", "A network must contain at least one bus.")
            return
        if not messagebox.askyesno("Delete bus", f"Delete bus {number} and connected branches/generators?"): return
        self._snapshot()
        self.net.buses = [b for b in self.net.buses if b.number != number]
        self.net.branches = [br for br in self.net.branches if br.from_bus != number and br.to_bus != number]
        self.net.generators = [g for g in self.net.generators if g.bus_number != number]
        self.net._index = {b.number:i for i,b in enumerate(self.net.buses)}
        self.bus_positions.pop(number, None)
        self.selected_bus = None; self.results = None
        # Network requires exactly one slack bus. If deleted slack, make first bus slack.
        if not any(b.in_service and b.kind.upper()=="SLACK" for b in self.net.buses):
            self.net.buses[0].kind = "SLACK"
        self.draw_network(); self.status.set(f"Bus {number} deleted; results are stale.")

    def draw_network(self):
        if not hasattr(self, "canvas") or self.net is None: return
        c = self.canvas; c.delete("all")
        w = max(c.winfo_width(), 600); h = max(c.winfo_height(), 350)
        # Ensure every bus has a location.
        for i,b in enumerate(self.net.buses):
            if b.number not in self.bus_positions:
                self.bus_positions[b.number] = (w/2 + min(w,h)*.32*math.cos(2*math.pi*i/max(len(self.net.buses),1)),
                                                h/2 + min(w,h)*.32*math.sin(2*math.pi*i/max(len(self.net.buses),1)))
        for br in self.net.branches:
            if br.from_bus not in self.bus_positions or br.to_bus not in self.bus_positions: continue
            x1,y1 = self.bus_positions[br.from_bus]; x2,y2 = self.bus_positions[br.to_bus]
            color = "#94a3b8" if not br.in_service else "#475569"
            c.create_line(x1,y1,x2,y2,fill=color,width=2,dash=(5,3) if not br.in_service else ())
            c.create_text((x1+x2)/2, (y1+y2)/2-8, text=f"{br.from_bus}-{br.to_bus}", fill="#64748b", font=("Segoe UI",8))
            if self.results and self.show_result_boxes.get():
                rr = next((r for r in self.results.get("branch_results",[]) if r["from_bus"]==br.from_bus and r["to_bus"]==br.to_bus), None)
                if rr:
                    c.create_text((x1+x2)/2,(y1+y2)/2+8,text=f"P={rr['p_from_mw']:.1f} MW\nQ={rr['q_from_mvar']:.1f} MVAr",font=("Segoe UI",8),fill="#0f766e")
        for b in self.net.buses:
            x,y = self.bus_positions[b.number]
            color = "#64748b" if not b.in_service else "#2563eb"
            if self.colour_coding.get() and self.results and b.in_service:
                vm = next((r["vm_pu"] for r in self.results["bus_results"] if r["bus"]==b.number), b.vm)
                if vm < b.v_min: color="#dc2626"
                elif vm > b.v_max: color="#ea580c"
            if self.selected_bus == b.number: color="#7c3aed"
            c.create_line(x-25,y,x+25,y,fill=color,width=6,dash=(5,3) if not b.in_service else ())
            c.create_text(x,y-17,text=f"{b.name or 'Bus '+str(b.number)} ({b.number})",fill="#0f172a",font=("Segoe UI",9,"bold"))
            c.create_text(x,y+18,text=b.kind.upper(),fill=color,font=("Segoe UI",8))
            if self.results and self.show_result_boxes.get():
                rr = next((r for r in self.results["bus_results"] if r["bus"]==b.number),None)
                if rr: c.create_text(x,y+34,text=f"{rr['vm_pu']:.3f} pu  {rr['va_deg']:.2f}°",fill="#0f766e",font=("Segoe UI",8))
        c.create_text(12, h-12, anchor="sw", text="Legend: red = voltage below Vmin; orange = above Vmax; grey dashed = out of service", fill="#64748b",font=("Segoe UI",8))

    def check_network(self):
        issues = []
        if not self.net.buses: issues.append("Network has no buses.")
        if sum(b.in_service and b.kind.upper()=="SLACK" for b in self.net.buses) != 1:
            issues.append("Exactly one in-service slack bus is required.")
        nums = {b.number for b in self.net.buses}
        for br in self.net.branches:
            if br.from_bus not in nums or br.to_bus not in nums: issues.append(f"Dangling branch {br.from_bus}-{br.to_bus}.")
            if br.from_bus == br.to_bus: issues.append(f"Self-connected branch at bus {br.from_bus}.")
            if br.r == 0 and br.x == 0: issues.append(f"Zero impedance branch {br.from_bus}-{br.to_bus}.")
            if br.rate_mva < 0: issues.append(f"Negative branch rating {br.from_bus}-{br.to_bus}.")
        # Reachability from slack over in-service branches.
        active = {b.number for b in self.net.buses if b.in_service}
        slack = next((b.number for b in self.net.buses if b.in_service and b.kind.upper()=="SLACK"), None)
        graph = {n:set() for n in active}
        for br in self.net.branches:
            if br.in_service and br.from_bus in active and br.to_bus in active:
                graph[br.from_bus].add(br.to_bus); graph[br.to_bus].add(br.from_bus)
        seen=set()
        if slack is not None:
            stack=[slack]
            while stack:
                u=stack.pop()
                if u in seen: continue
                seen.add(u); stack.extend(graph.get(u,set())-seen)
        for n in sorted(active-seen): issues.append(f"Bus {n} is in an island without a path to the slack.")
        if issues:
            messagebox.showwarning("Network check failed", "\n".join(issues))
            self.status.set(f"Network check failed: {len(issues)} issue(s).")
            return False
        messagebox.showinfo("Network check", "Network checks passed.")
        self.status.set("Network checks passed.")
        return True

    def run_nr(self):
        if not self.check_network(): return
        try:
            tol = float(self.tolerance.get())
            maxit = int(self.max_iterations.get())
            if tol <= 0 or maxit < 1: raise ValueError("Tolerance and iteration limit must be positive.")
            # Scale loads on a temporary copy to preserve the saved input data.
            d = network_to_dict(self.net)
            run_net = network_from_dict(d)
            scale = float(self.load_scale.get())
            for b in run_net.buses:
                b.p_load *= scale; b.q_load *= scale
            if self.flat_start.get():
                initial_vm = [1.0 for _ in run_net.buses]
                initial_va = [0.0 for _ in run_net.buses]
                for i,b in enumerate(run_net.buses):
                    if b.kind.upper() in ("SLACK","PV"): initial_vm[i] = b.vm
                run_net.metadata["gui_initial_flat_start"] = True
            else:
                initial_vm = None; initial_va = None
            self.status.set("Solving Newton–Raphson load flow...")
            self.update_idletasks()
            result = solve_power_flow(run_net, tolerance=tol, max_iterations=maxit,
                                      enforce_q_limits=self.enforce_q.get(),
                                      initial_vm=initial_vm, initial_va_deg=initial_va)
            self.results = result
            self._display_results()
            self.draw_network()
            self.status.set(f"Converged in {result['iterations']} iterations | mismatch {result['max_mismatch_pu']:.3e} pu | {result['solve_time_s']*1000:.2f} ms | base {self.net.base_mva:g} MVA")
        except Exception as exc:
            self.results = None
            self.draw_network()
            self.status.set(f"Load flow failed: {exc}")
            messagebox.showerror("Load flow failed", str(exc))

    def _display_results(self):
        for tree in self.result_trees.values():
            tree.delete(*tree.get_children())
        if not self.results: return
        r = self.results
        for b in r["bus_results"]:
            limit = "OUT" if not self.net.buses[self.net.index[b["bus"]]].in_service else ("Q limit" if b.get("q_limit_hit") else ("V violation" if b["vm_pu"] < self.net.buses[self.net.index[b["bus"]]].v_min or b["vm_pu"] > self.net.buses[self.net.index[b["bus"]]].v_max else "OK"))
            self.result_trees["Bus results"].insert("", "end", values=(b["bus"],b["type"],f'{b["vm_pu"]:.5f}',f'{b["vm_kv"]:.3f}',f'{b["va_deg"]:.3f}',f'{b["p_injection_mw"]:.4f}',f'{b["q_injection_mvar"]:.4f}',limit))
            if limit not in ("OK",): self.result_trees["Violations"].insert("", "end", values=(f'Bus {b["bus"]}',limit,f'{b["vm_pu"]:.4f} pu'))
        for br in r["branch_results"]:
            loading = br["loading_percent"]
            loading_s = "N/A" if loading is None else f"{loading:.2f}"
            self.result_trees["Branch results"].insert("", "end", values=(br["from_bus"],br["to_bus"],f'{br["p_from_mw"]:.3f}',f'{br["q_from_mvar"]:.3f}',f'{br["p_to_mw"]:.3f}',f'{br["q_to_mvar"]:.3f}',loading_s,f'{br["p_loss_mw"]:.4f}',f'{br["q_loss_mvar"]:.4f}'))
            if loading is not None and loading >= 80:
                self.result_trees["Violations"].insert("", "end", values=(f'Branch {br["from_bus"]}-{br["to_bus"]}',"Over 80% rating",f"{loading:.2f}%"))
        for g in r.get("generator_results",[]):
            self.result_trees["Generators"].insert("", "end", values=(g["bus"],g["name"],f'{g["p_gen_mw"]:.4f}',f'{g["q_gen_mvar"]:.4f}',g["in_service"]))
        for item in r.get("iteration_log",[]):
            self.result_trees["Iteration log"].insert("", "end", values=(item["iteration"],f'{item["max_mismatch_pu"]:.5e}'))

    def refresh_results(self):
        if self.results: self._display_results()
        else:
            for tree in self.result_trees.values(): tree.delete(*tree.get_children())
            self.status.set("No current results. Run load flow to populate result tables.")

    def _mark_stale(self):
        self.results = None
        self.refresh_results()

    def apply_base(self):
        try:
            value=float(self.base_mva_var.get())
            if value <= 0: raise ValueError("Base MVA must be positive.")
            self.net.base_mva=value; self.results=None; self.status.set(f"System base set to {value:g} MVA. Entered MW/MVAr values remain physical.")
        except Exception as exc: messagebox.showerror("Invalid base",str(exc))

    def show_settings(self):
        messagebox.showinfo("Load flow settings", f"Tolerance: {self.tolerance.get()}\nMaximum iterations: {self.max_iterations.get()}\nFlat start: {self.flat_start.get()}\nQ limits enforced: {self.enforce_q.get()}\nGlobal load scale: {self.load_scale.get():.2f}")

    def import_case(self):
        path=filedialog.askopenfilename(title="Import MATPOWER case", filetypes=[("MATPOWER case","*.m"),("All files","*.*")], initialdir=str(Path(__file__).parent/"data"))
        if not path: return
        try:
            self._snapshot()
            self.net=load_matpower_case(path)
            self.base_mva_var.set(self.net.base_mva)
            self.results=None; self.project_path=None
            self._restore_positions()
            self.title(f"{APP_TITLE} — {Path(path).name}")
            self.draw_network(); self.refresh_results()
            self.status.set(f"Imported MATPOWER case: {Path(path).name}")
        except Exception as exc: messagebox.showerror("Import failed",str(exc))

    def save_project(self):
        if not self.project_path: return self.save_as()
        self._write_project(self.project_path)

    def save_as(self):
        path=filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("Load-flow project","*.json")], initialfile="project.json")
        if path:
            self.project_path=Path(path); self._write_project(self.project_path)

    def _write_project(self,path):
        try:
            payload={"format":"E4234-LoadFlow-Project","version":1,"network":network_to_dict(self.net),
                     "layout":{str(k):list(v) for k,v in self.bus_positions.items()},
                     "study":{"tolerance":self.tolerance.get(),"max_iterations":self.max_iterations.get(),
                              "flat_start":self.flat_start.get(),"enforce_q_limits":self.enforce_q.get(),
                              "load_scale":self.load_scale.get()}}
            Path(path).write_text(json.dumps(payload,indent=2),encoding="utf-8")
            self.title(f"{APP_TITLE} — {Path(path).name}"); self.status.set(f"Saved project: {path}")
        except Exception as exc: messagebox.showerror("Save failed",str(exc))

    def open_project(self):
        path=filedialog.askopenfilename(filetypes=[("Load-flow project","*.json"),("All files","*.*")])
        if not path:return
        try:
            payload=json.loads(Path(path).read_text(encoding="utf-8"))
            self.net=network_from_dict(payload["network"])
            self.project_path=Path(path); self.results=None
            self.bus_positions={int(k):tuple(v) for k,v in payload.get("layout",{}).items()}
            study=payload.get("study",{})
            self.tolerance.set(study.get("tolerance","1e-8")); self.max_iterations.set(study.get("max_iterations",30))
            self.flat_start.set(study.get("flat_start",False)); self.enforce_q.set(study.get("enforce_q_limits",False))
            self.load_scale.set(study.get("load_scale",1.0)); self.base_mva_var.set(self.net.base_mva)
            self._restore_missing_positions()
            self.title(f"{APP_TITLE} — {Path(path).name}")
            self.draw_network(); self.refresh_results(); self.status.set(f"Opened project: {path}")
        except Exception as exc: messagebox.showerror("Open failed",str(exc))

    def _restore_missing_positions(self):
        w=max(self.canvas.winfo_width(),600); h=max(self.canvas.winfo_height(),350)
        for i,b in enumerate(self.net.buses):
            if b.number not in self.bus_positions:
                a=2*math.pi*i/max(len(self.net.buses),1)
                self.bus_positions[b.number]=(w/2+min(w,h)*.32*math.cos(a),h/2+min(w,h)*.32*math.sin(a))

    def auto_layout(self):
        self._restore_positions(); self.draw_network(); self.status.set("Automatic circular layout applied.")

    def export_csv(self):
        if not self.results:
            messagebox.showwarning("No results","Run load flow before exporting results."); return
        directory=filedialog.askdirectory(title="Choose folder for CSV results")
        if not directory:return
        try:
            for name, rows in [
                ("bus_results",self.results["bus_results"]),
                ("branch_results",self.results["branch_results"]),
                ("generator_results",self.results.get("generator_results",[])),
                ("iteration_log",self.results.get("iteration_log",[])),
            ]:
                if not rows: continue
                with open(Path(directory)/(name+".csv"),"w",newline="",encoding="utf-8-sig") as f:
                    writer=csv.DictWriter(f,fieldnames=list(rows[0].keys())); writer.writeheader(); writer.writerows(rows)
            self.status.set(f"CSV results exported to {directory}")
            messagebox.showinfo("Export complete",f"CSV files written to:\n{directory}")
        except Exception as exc: messagebox.showerror("Export failed",str(exc))

    def export_html(self):
        if not self.results:
            messagebox.showwarning("No results","Run load flow before exporting report."); return
        path=filedialog.asksaveasfilename(defaultextension=".html",filetypes=[("HTML report","*.html")],initialfile="load_flow_report.html")
        if not path:return
        try:
            def table(title, rows):
                if not rows:return f"<h2>{title}</h2><p>No data</p>"
                cols=list(rows[0])
                head="".join(f"<th>{x}</th>" for x in cols)
                body="".join("<tr>"+"".join(f"<td>{row.get(x,'')}</td>" for x in cols)+"</tr>" for row in rows)
                return f"<h2>{title}</h2><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
            r=self.results
            html=f"""<!doctype html><html><head><meta charset="utf-8"><title>Load Flow Report</title>
            <style>body{{font-family:Arial;margin:28px;color:#172033}}table{{border-collapse:collapse;width:100%;margin-bottom:22px;font-size:12px}}td,th{{border:1px solid #cbd5e1;padding:6px;text-align:right}}th{{background:#e2e8f0}}h1{{color:#1d4ed8}}</style></head><body>
            <h1>E4 234 Load Flow Report</h1><p>Converged: {r['converged']} | Iterations: {r['iterations']} | Maximum mismatch: {r['max_mismatch_pu']:.5e} pu | Active losses: {r['total_p_loss_mw']:.4f} MW | Reactive losses: {r['total_q_loss_mvar']:.4f} MVAr</p>
            {table('Bus results',r['bus_results'])}{table('Branch results',r['branch_results'])}{table('Generator results',r.get('generator_results',[]))}{table('Iteration log',r.get('iteration_log',[]))}
            </body></html>"""
            Path(path).write_text(html,encoding="utf-8")
            self.status.set(f"HTML report exported: {path}")
            messagebox.showinfo("Export complete",f"Report saved to:\n{path}")
        except Exception as exc: messagebox.showerror("Export failed",str(exc))

    def user_guide(self):
        messagebox.showinfo("User guide",
            "1. Import a MATPOWER .m case or create a new project.\n"
            "2. Use the palette to add buses and branches.\n"
            "3. Select a bus and edit its properties.\n"
            "4. Check network, then run NR (F5).\n"
            "5. Inspect bus, branch, generator, violation and iteration tables.\n"
            "6. Save project JSON and export CSV or HTML results.\n\n"
            "New branches use placeholder R/X values. Edit these before running a custom network.")


if __name__ == "__main__":
    app = LoadFlowApp()
    app.mainloop()
