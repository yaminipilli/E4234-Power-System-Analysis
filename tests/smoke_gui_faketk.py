"""Smoke-run gui_app.py against a fake tkinter (this sandbox has no Tk).

It cannot check how the window LOOKS, but it executes the real event handlers (place / connect / drag /
edit / run / export / undo ...) with a mini canvas that supports hit-testing, so NameErrors, bad attribute
names and logic slips are caught.  Run:  python tests/smoke_gui_faketk.py
"""
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class M(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Fake()


class Fake(metaclass=M):
    def __init__(self, *a, **k):
        self._opts = dict(k)
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Fake()
    def __call__(self, *a, **k):
        return Fake()
    def __iter__(self):
        return iter(())
    def __bool__(self):
        return True
    def __float__(self):
        return 0.0
    def __int__(self):
        return 0
    def __index__(self):
        return 0
    def _op(self, o):
        return 0.0
    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = __truediv__ = __rtruediv__ = _op
    __gt__ = __lt__ = __ge__ = __le__ = lambda self, o: False


class Var:
    default = ""

    def __init__(self, master=None, value=None, **k):
        self.v = self.default if value is None else value
    def get(self):
        return self.v
    def set(self, v):
        self.v = v
    def trace_add(self, *a):
        pass


class BoolVar(Var):
    default = False


class FakeCanvas(Fake):
    def __init__(self, *a, **k):
        self.items, self.next = {}, 1
        self.w, self.h = 900, 560
    def _new(self, kind, coords, k):
        i, self.next = self.next, self.next + 1
        tags = k.get("tags", ())
        tags = (tags,) if isinstance(tags, str) else tuple(tags)
        self.items[i] = (kind, [float(c) for c in coords if isinstance(c, (int, float))], tags, k)
        return i
    def create_line(self, *c, **k): return self._new("line", c, k)
    def create_rectangle(self, *c, **k): return self._new("rect", c, k)
    def create_oval(self, *c, **k): return self._new("oval", c, k)
    def create_polygon(self, *c, **k): return self._new("poly", c, k)
    def create_text(self, *c, **k): return self._new("text", c[:2], k)
    def create_window(self, *c, **k): return self._new("win", c[:2], k)
    def delete(self, *tags):
        if tags and tags[0] == "all":
            self.items.clear()
        else:
            for i in [i for i, it in self.items.items() if set(tags) & set(it[2])]:
                del self.items[i]
    def bbox(self, *a):
        if not a or a[0] == "all":
            return None
        it = self.items.get(a[0])
        if not it or not it[1]:
            return None
        xs, ys = it[1][0::2], it[1][1::2]
        if it[0] == "text":
            return (xs[0], ys[0], xs[0] + 60, ys[0] + 24)
        return (min(xs), min(ys), max(xs), max(ys))
    def find_overlapping(self, x1, y1, x2, y2):
        out = []
        for i, (kind, co, tags, k) in self.items.items():
            if not co or not tags:
                continue
            xs, ys = co[0::2], co[1::2]
            if kind == "text":
                continue
            if min(xs) <= x2 and max(xs) >= x1 and min(ys) <= y2 and max(ys) >= y1:
                if kind == "line" and len(xs) == 2 and abs(xs[0] - xs[1]) > 1 and abs(ys[0] - ys[1]) > 1:
                    # diagonal line: distance test
                    (ax, ay), (bx, by) = (xs[0], ys[0]), (xs[1], ys[1])
                    t = max(0, min(1, ((x1 + 5 - ax) * (bx - ax) + (y1 + 5 - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)))
                    px, py = ax + t * (bx - ax), ay + t * (by - ay)
                    if ((px - (x1 + 5)) ** 2 + (py - (y1 + 5)) ** 2) ** 0.5 > 6:
                        continue
                out.append(i)
        return out
    def gettags(self, i): return self.items[i][2] if i in self.items else ()
    def winfo_width(self): return self.w
    def winfo_height(self): return self.h


def fake_module(name, **attrs):
    m = types.ModuleType(name)
    def ga(a):
        if a.startswith("__"):
            raise AttributeError(a)
        c = M(a, (Fake,), {})
        setattr(m, a, c)
        return c
    m.__getattr__ = ga
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


class TclError(Exception):
    pass


ttk = fake_module("tkinter.ttk")
filedialog = fake_module("tkinter.filedialog")
messagebox = fake_module("tkinter.messagebox")
simpledialog = fake_module("tkinter.simpledialog")
tk = fake_module("tkinter", Canvas=FakeCanvas, StringVar=Var, BooleanVar=BoolVar, IntVar=Var, DoubleVar=Var,
                 TclError=TclError, ttk=ttk, filedialog=filedialog, messagebox=messagebox, simpledialog=simpledialog)
sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk, "tkinter.filedialog": filedialog,
                    "tkinter.messagebox": messagebox, "tkinter.simpledialog": simpledialog,
                    "matplotlib.backends.backend_tkagg": fake_module("matplotlib.backends.backend_tkagg")})

popups = []
for name in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, name, lambda title, msg, _n=name, **k: popups.append((_n, title, msg)))
messagebox.askyesno = lambda *a, **k: True
messagebox.askyesnocancel = lambda *a, **k: False
simpledialog.askstring = lambda *a, **k: "7"

import gui_app                                     # noqa: E402


class Ev:
    def __init__(self, x=0, y=0, state=0, delta=0, xr=0, yr=0):
        self.x, self.y, self.state, self.delta, self.x_root, self.y_root = x, y, state, delta, xr, yr


def click(app, sx, sy, ctrl=False):
    app.on_press(Ev(sx, sy, 4 if ctrl else 0))
    app.on_release(Ev(sx, sy))


def screen_of_bus(app, n, dx=10, dy=0):
    x0, x1, y = app._bus_extent(n)
    sx, sy = app.w2s(x0 + dx, y)
    return sx, sy


def main():
    app = gui_app.LoadFlowApp()
    app.canvas.w, app.canvas.h = 900, 560
    print("app constructed; bus count:", len(app.model.net.buses))

    # ---- build a network with the palette (click-to-place) -----------------------------------------
    app.place("Bus", 600, 150)
    app.place("Bus", 600, 400)
    assert len(app.model.net.buses) == 3
    app.set_tool("Line")
    click(app, *screen_of_bus(app, 1)); click(app, *screen_of_bus(app, 2))
    app.set_tool("Transformer")
    click(app, *screen_of_bus(app, 2)); click(app, *screen_of_bus(app, 3))
    assert [b.kind for b in app.model.net.branches] == ["line", "transformer"], app.model.net.branches
    app.set_tool("Load"); click(app, *screen_of_bus(app, 3))
    app.set_tool("Generator"); click(app, *screen_of_bus(app, 2))
    assert app.model.net.generators and app.model.net.buses[2].p_load > 0
    print("built: buses", len(app.model.net.buses), "branches", len(app.model.net.branches),
          "gens", len(app.model.net.generators))
    assert not app.model.errors(), app.model.errors()

    # ---- palette drag and drop ------------------------------------------------------------------
    app.winfo_containing = lambda x, y: app.canvas
    app.canvas.winfo_rootx = lambda: 100
    app.canvas.winfo_rooty = lambda: 50
    app._palette_press("Bus", Ev(xr=10, yr=10)); app._palette_motion(Ev(xr=300, yr=300))
    app._palette_release(Ev(xr=400, yr=450))
    assert len(app.model.net.buses) == 4
    app.delete_selected()                                  # the new bus is selected -> delete
    assert len(app.model.net.buses) == 3

    # ---- run, tables, plot ----------------------------------------------------------------------------
    app.run_nr()
    res = app.model.results
    assert res and res["converged"], res and res["message"]
    t = app.tables["Bus results"]
    assert len(t.rows) == 3 and app.tables["Branch results"].rows
    print("run ok:", res["iterations"], "iterations; popups:", popups)

    # ---- select / property editing / validation / stale ---------------------------------------------
    app.sel = {("branch", 0)}; app.show_properties()
    form = app.prop_form
    form.vars["rate_mva"].set("-5"); assert not form.apply()
    form.vars["rate_mva"].set("20"); assert form.apply()
    assert app.model.stale
    app.run_nr()
    br = app.model.results["branch_results"][0]
    assert br["loading_percent"] and br["loading_percent"] > 0
    app.redraw()
    print("loading % after rating edit:", round(br["loading_percent"], 1))

    # ---- drag a bus + undo --------------------------------------------------------------------------------
    before = app.model.positions[2]
    sx, sy = screen_of_bus(app, 2, dx=30)
    app.on_press(Ev(sx, sy)); app.on_motion(Ev(sx + 80, sy + 40)); app.on_release(Ev(sx + 80, sy + 40))
    assert app.model.positions[2] != before
    app.undo(); assert app.model.positions[2] == before
    app.redo(); app.undo()

    # ---- IEEE-14 import, run with Q limits, divergence ----------------------------------------------------
    app.open_example()
    assert len(app.model.net.buses) == 14
    app.var_qlim.set(True); app.model.net.generators[1].q_max = 0.3
    app.run_nr()
    assert app.model.results["q_limit_switches"], "expected a PV->PQ switch"
    app.var_scale.set("40"); app.var_qlim.set(False)
    popups.clear(); app.run_nr()
    assert not app.model.results["converged"] and any("DIVERGED" in p[1] for p in popups), popups
    app.redraw()
    app.var_scale.set("1.0"); app.run_nr(); assert app.model.results["converged"]

    # ---- selection tools, copy/paste, find, context menu -------------------------------------------------
    app.select_all(); assert len(app.sel) == 14 + 20
    app.sel = {("bus", 4), ("bus", 5)}; app.copy_selected(); app.paste()
    assert len(app.model.net.buses) == 16
    app.find_element(); assert ("bus", 7) in app.sel
    app.on_context(Ev(*screen_of_bus(app, 7)))
    app.set_service(False); app.set_service(True)
    app.zoom_at(400, 300, 1.3); app.fit_view(); app.on_wheel(Ev(400, 300, delta=120))

    # ---- exports and files ------------------------------------------------------------------------------------
    while len(app.model.net.buses) > 14:
        app.undo()
    app.run_nr()
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for fn, ext in ((app.export_report, "pdf"), (app.export_report, "html"), (app.export_results, "xlsx"),
                        (app.export_results, "csv"), (app.export_matpower, "m"), (app.file_save_as, "json")):
            filedialog.asksaveasfilename = lambda *a, _p=str(d / f"out.{ext}"), **k: _p
            fn()
            target = d / (f"out.{ext}" if ext != "csv" else "out")
            assert target.exists(), (fn.__name__, ext, popups[-3:])
        filedialog.askopenfilename = lambda *a, **k: str(d / "out.json")
        app.file_open()
        assert len(app.model.net.buses) == 14 and app.model.positions
        app.run_nr(); assert app.model.results["converged"]
    app.file_new(); assert len(app.model.net.buses) == 1
    app.run_nr(); popups.clear(); app.show_results(); assert not popups                     # results exist -> no "run first" box
    app.model.results = None; app.show_results(); assert popups and "no results" in popups[-1][2].lower()
    app.auto_layout("force"); app.auto_layout("circle")
    app.sel = {("bus", 3)}; app.show_properties(); app.focus_properties()
    app.user_guide(); app.about(); app.show_loss_summary(); app.check_network(); app.reset_layout()
    app.auto_layout()
    print("errors popped up during smoke run:", [p for p in popups if p[0] == "showerror"])
    print("GUI smoke test (fake Tk) finished OK")


if __name__ == "__main__":
    main()
