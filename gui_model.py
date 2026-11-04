"""Project model behind the GUI: network + layout + study settings + undo/redo + results.

All editing logic lives here (no Tk), so it can be unit-tested; ``gui_app.py`` is a thin view.
Selection keys:  ('bus', number)  ('branch', index)  ('gen', index)
"""
import copy
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import gui_fields
from layout import circular_layout, force_directed_layout, snap
from matpower_import import load_matpower_case
from network import Branch, Bus, Generator, Network, derive_bus_kinds
from solver import solve_power_flow
import units

MAX_UNDO = 50
PROJECT_FORMAT = "E4234-LoadFlow-Project"


@dataclass
class Study:
    tolerance: float = 1e-8
    max_iterations: int = 30
    start: str = "flat"              # 'flat' | 'previous' | 'data'
    enforce_q_limits: bool = False
    load_scale: float = 1.0
    show_boxes: bool = True
    show_colours: bool = True
    show_grid: bool = True
    show_kv: bool = False
    snap_to_grid: bool = True

    @classmethod
    def from_dict(cls, d):
        """Tolerant reader: also understands the study block written by the first version of the GUI
        (string tolerance, 'flat_start' flag)."""
        s = cls()

        def num(key, conv, default):
            try:
                return conv(d[key])
            except (KeyError, TypeError, ValueError):
                return default

        s.tolerance = num("tolerance", float, s.tolerance)
        s.max_iterations = num("max_iterations", lambda v: int(float(v)), s.max_iterations)
        s.load_scale = num("load_scale", float, s.load_scale)
        s.enforce_q_limits = bool(d.get("enforce_q_limits", s.enforce_q_limits))
        if d.get("start") in ("flat", "previous", "data"):
            s.start = d["start"]
        elif "flat_start" in d:
            s.start = "flat" if d["flat_start"] else "data"
        for k in ("show_boxes", "show_colours", "show_grid", "show_kv", "snap_to_grid"):
            if k in d:
                setattr(s, k, bool(d[k]))
        return s


def _jsonable(o):
    """json fallback: NumPy scalars -> Python numbers."""
    import numpy as np
    if isinstance(o, np.generic):
        return o.item()
    raise TypeError(f"{type(o).__name__} is not JSON serializable")


def network_to_dict(net):
    return {"base_mva": net.base_mva, "frequency_hz": net.frequency_hz, "metadata": net.metadata,
            "buses": [asdict(x) for x in net.buses], "branches": [asdict(x) for x in net.branches],
            "generators": [asdict(x) for x in net.generators]}


def migrate_legacy_generation(net):
    """Projects saved by the first GUI kept generation on the Bus fields (no Generator records).
    Convert them: PV buses get a Generator record; PQ buses with a scheduled injection become a net load."""
    if net.generators:
        return
    for b in net.buses:
        if b.kind.upper() == "SLACK":
            continue
        if b.kind.upper() == "PV":
            net.generators.append(Generator(b.number, p_gen=b.p_gen, q_gen=b.q_gen, q_max=b.q_max, q_min=b.q_min,
                                            v_setpoint=b.vm, name=f"Generator at bus {b.number}"))
            b.p_gen = b.q_gen = 0.0
        elif (b.p_gen or b.q_gen) and b.load_model.upper() == "PQ":
            b.p_load -= b.p_gen
            b.q_load -= b.q_gen
            b.p_gen = b.q_gen = 0.0


def network_from_dict(d):
    meta = dict(d.get("metadata", {}))
    if isinstance(meta.get("base_kv_by_bus"), dict):          # JSON turns int keys into strings
        meta["base_kv_by_bus"] = {int(k): v for k, v in meta["base_kv_by_bus"].items()}
    return Network(buses=[Bus(**x) for x in d["buses"]], branches=[Branch(**x) for x in d.get("branches", [])],
                   generators=[Generator(**x) for x in d.get("generators", [])],
                   base_mva=float(d.get("base_mva", 100)), frequency_hz=float(d.get("frequency_hz", 50)),
                   metadata=meta)


class ProjectModel:
    def __init__(self, net=None, positions=None):
        self.net = net or Network(buses=[Bus(1, "SLACK", name="Bus 1", base_kv=11.0)], branches=[])
        self.positions = positions if positions is not None else circular_layout(self.net)
        self.study = Study()
        self.results = None
        self.stale = False
        self.path = None
        self.dirty = False
        self.undo_stack, self.redo_stack = [], []
        self.clipboard = None

    # ------------------------------------------------------------------ undo / redo
    def snapshot(self):
        return json.dumps({"net": network_to_dict(self.net),
                           "pos": {str(k): list(v) for k, v in self.positions.items()}}, default=_jsonable)

    def _restore(self, text):
        d = json.loads(text)
        self.net = network_from_dict(d["net"])
        self.positions = {int(k): tuple(v) for k, v in d["pos"].items()}

    def push_undo(self, before):
        self.undo_stack.append(before)
        del self.undo_stack[:-MAX_UNDO]
        self.redo_stack.clear()
        self.touch()

    def touch(self):
        self.dirty = True
        if self.results is not None:
            self.stale = True            # B6.4: editing greys out the results until the next run

    def undo(self):
        if not self.undo_stack:
            return False
        self.redo_stack.append(self.snapshot())
        self._restore(self.undo_stack.pop())
        self.touch()
        return True

    def redo(self):
        if not self.redo_stack:
            return False
        self.undo_stack.append(self.snapshot())
        self._restore(self.redo_stack.pop())
        self.touch()
        return True

    # ------------------------------------------------------------------ editing
    def add_bus(self, x, y):
        before = self.snapshot()
        n = max((b.number for b in self.net.buses), default=0) + 1
        self.net.buses.append(Bus(n, "PQ", name=f"Bus {n}", base_kv=11.0))
        self.net.rebuild_index()
        self.positions[n] = (snap(x), snap(y)) if self.study.snap_to_grid else (x, y)
        self.push_undo(before)
        return n

    def add_branch(self, kind, a, b):
        if a == b:
            raise ValueError("a branch needs two different buses")
        before = self.snapshot()
        if kind.lower() == "transformer":
            br = Branch(a, b, 0.0, 0.0, kind="transformer", name=f"Transformer {a}-{b}", input_mode="nameplate",
                        kv_hv=self.net.bus_kv(a), kv_lv=self.net.bus_kv(b))
            br.r, br.x = units.transformer_pu_from_nameplate(br.rated_mva, br.z_pct, br.x_r, self.net.base_mva)
            br.rate_mva = br.rated_mva
        else:
            br = Branch(a, b, 0.01, 0.1, 0.0, rate_mva=100.0, name=f"Line {a}-{b}")
        self.net.branches.append(br)
        self.push_undo(before)
        return len(self.net.branches) - 1

    def add_element(self, kind, bus):
        """kind: generator | load | shunt | external grid.  Returns the new selection key."""
        before = self.snapshot()
        b = self.net.buses[self.net.index[bus]]
        k = kind.lower()
        key = ("bus", bus)
        if k == "generator":
            self.net.generators.append(Generator(bus, p_gen=0.5, v_setpoint=b.vm, q_min=-1.0, q_max=1.0,
                                                 mbase_mva=self.net.base_mva, name=f"Generator at bus {bus}"))
            key = ("gen", len(self.net.generators) - 1)
        elif k == "load":
            b.p_load += 20.0 / self.net.base_mva
            b.q_load += 5.0 / self.net.base_mva
        elif k == "shunt":
            b.b_shunt += 10.0 / self.net.base_mva
        elif k in ("external grid", "extgrid"):
            for o in self.net.buses:
                if o.kind.upper() == "SLACK":
                    o.kind = "PQ"
            b.kind, b.va_deg = "SLACK", 0.0
        else:
            raise ValueError(kind)
        derive_bus_kinds(self.net)
        self.push_undo(before)
        return key

    def delete(self, selection):
        if not selection:
            return
        before = self.snapshot()
        net = self.net
        buses = {k for kind, k in selection if kind == "bus"}
        branches = {k for kind, k in selection if kind == "branch"}
        gens = {k for kind, k in selection if kind == "gen"}
        net.branches = [br for i, br in enumerate(net.branches)
                        if i not in branches and br.from_bus not in buses and br.to_bus not in buses]
        net.generators = [g for i, g in enumerate(net.generators) if i not in gens and g.bus_number not in buses]
        net.buses = [b for b in net.buses if b.number not in buses]
        net.rebuild_index()
        for n in buses:
            self.positions.pop(n, None)
        derive_bus_kinds(net)
        self.push_undo(before)

    def set_in_service(self, selection, flag):
        before = self.snapshot()
        for kind, key in selection:
            gui_el = {"bus": lambda: self.net.buses[self.net.index[key]], "branch": lambda: self.net.branches[key],
                      "gen": lambda: self.net.generators[key]}[kind]()
            gui_el.in_service = flag
        derive_bus_kinds(self.net)
        self.push_undo(before)

    def move_buses(self, new_xy, before=None):
        before = before or self.snapshot()
        for n, (x, y) in new_xy.items():
            self.positions[n] = (snap(x), snap(y)) if self.study.snap_to_grid else (x, y)
        self.push_undo(before)

    def apply_fields(self, kind, key, values):
        before = self.snapshot()
        errs = gui_fields.apply(self.net, kind, key, values)
        if errs:
            return errs
        self.push_undo(before)
        return {}

    def set_base(self, base_mva, freq):
        if base_mva <= 0 or freq <= 0:
            raise ValueError("base MVA and frequency must be positive")
        before = self.snapshot()
        self.net.base_mva, self.net.frequency_hz = base_mva, freq
        self.push_undo(before)

    def auto_layout(self, mode="circle"):
        """mode: 'circle' (default) or 'force' (force-directed)."""
        before = self.snapshot()
        self.positions = force_directed_layout(self.net) if mode == "force" else circular_layout(self.net)
        self.push_undo(before)

    def find(self, text):
        t = text.strip().lower()
        hits = []
        if not t:
            return hits
        for b in self.net.buses:
            if t == str(b.number) or t in b.name.lower():
                hits.append(("bus", b.number))
        for i, br in enumerate(self.net.branches):
            kind = "transformer" if br.kind.lower() == "transformer" else "line"
            endpoints = f"{br.from_bus}-{br.to_bus}"
            reverse_endpoints = f"{br.to_bus}-{br.from_bus}"
            searchable = f"{br.name} {kind} {br.from_bus} {br.to_bus} {endpoints} {reverse_endpoints}".lower()
            if t in searchable:
                hits.append(("branch", i))
        for i, g in enumerate(self.net.generators):
            if t in g.name.lower():
                hits.append(("gen", i))
        return hits

    # ------------------------------------------------------------------ copy / paste
    def copy(self, selection):
        buses = {k for kind, k in selection if kind == "bus"}
        if not buses:
            self.clipboard = None
            return 0
        n = self.net
        self.clipboard = {
            "buses": [copy.deepcopy(b) for b in n.buses if b.number in buses],
            "xy": {b: self.positions[b] for b in buses},
            "branches": [copy.deepcopy(b) for b in n.branches if b.from_bus in buses and b.to_bus in buses],
            "generators": [copy.deepcopy(g) for g in n.generators if g.bus_number in buses]}
        return len(buses)

    def paste(self, offset=(40.0, 40.0)):
        if not self.clipboard:
            return []
        before = self.snapshot()
        c = copy.deepcopy(self.clipboard)
        base = max((b.number for b in self.net.buses), default=0)
        remap = {b.number: base + i for i, b in enumerate(sorted(c["buses"], key=lambda b: b.number), 1)}
        sel = []
        for b in c["buses"]:
            old = b.number
            b.number = remap[old]
            b.name = f"{b.name} (copy)"
            if b.kind.upper() == "SLACK":
                b.kind = "PQ"                    # a network has exactly one slack
            self.net.buses.append(b)
            x, y = c["xy"][old]
            self.positions[b.number] = (x + offset[0], y + offset[1])
            sel.append(("bus", b.number))
        for br in c["branches"]:
            br.from_bus, br.to_bus = remap[br.from_bus], remap[br.to_bus]
            self.net.branches.append(br)
        for g in c["generators"]:
            g.bus_number = remap[g.bus_number]
            self.net.generators.append(g)
        self.net.rebuild_index()
        derive_bus_kinds(self.net)
        self.push_undo(before)
        return sel

    # ------------------------------------------------------------------ study
    def issues(self):
        derive_bus_kinds(self.net)
        return self.net.check()

    def errors(self):
        return [i for i in self.issues() if i.level == "error"]

    def can_run(self):
        return not self.errors()

    def run(self, callback=None):
        """Run NR with the study settings.  Loads are scaled on a COPY so the saved data is untouched."""
        derive_bus_kinds(self.net)
        s = self.study
        run_net = network_from_dict(network_to_dict(self.net))
        for b in run_net.buses:
            b.p_load *= s.load_scale
            b.q_load *= s.load_scale
        kw = dict(flat_start=True)
        if s.start == "previous" and self.results is not None and len(self.results["vm"]) == len(run_net.buses):
            kw = dict(flat_start=False, initial_vm=self.results["vm"], initial_va_deg=self.results["va_deg"])
        elif s.start == "data":
            kw = dict(flat_start=False)
        res = solve_power_flow(run_net, tolerance=s.tolerance, max_iterations=s.max_iterations,
                               enforce_q_limits=s.enforce_q_limits, callback=callback, **kw)
        self.results = res
        self.stale = False
        return res

    # ------------------------------------------------------------------ files
    def project_dict(self):
        return {"format": PROJECT_FORMAT, "version": 1, "network": network_to_dict(self.net),
                "layout": {str(k): list(v) for k, v in self.positions.items()}, "study": asdict(self.study)}

    def save(self, path=None):
        path = path or self.path
        if not path:
            raise ValueError("no file name")
        Path(path).write_text(json.dumps(self.project_dict(), indent=1, default=_jsonable), encoding="utf-8")
        self.path, self.dirty = Path(path), False

    @classmethod
    def open(cls, path):
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if "network" not in d:
            raise ValueError("this file is not a load-flow project (no 'network' section)")
        net = network_from_dict(d["network"])
        migrate_legacy_generation(net)
        derive_bus_kinds(net)
        pos = {int(k): tuple(v) for k, v in d.get("layout", {}).items() if int(k) in net.index}
        m = cls(net, pos)
        missing = [b.number for b in net.buses if b.number not in pos]
        if missing:
            m.positions.update({k: v for k, v in circular_layout(net).items() if k in missing})
        m.study = Study.from_dict(d.get("study", {}))
        m.path = Path(path)
        return m

    @classmethod
    def import_case(cls, path):
        low = str(path).lower()
        if low.endswith((".cdf", ".txt", ".dat")):
            from cdf_import import load_cdf_case
            net = load_cdf_case(path)
        else:
            net = load_matpower_case(path)
        m = cls(net, circular_layout(net))          # imported cases start on a circle (Network > Auto-layout to change)
        m.dirty = True
        return m
