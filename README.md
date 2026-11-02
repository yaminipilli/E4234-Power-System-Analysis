# E4 234 — Newton–Raphson Load Flow Simulator (Part A engine + Part B Tkinter GUI)

Python 3.10+.  The solver uses NumPy and SciPy only — no ready-made power-flow routine in the solve path.

## Install and run
```
python -m pip install -r requirements.txt
python gui_app.py                    # GUI  (or double-click start_gui.bat);  File > Open example: IEEE 14-bus
python -m unittest discover -s tests -v      # test suite (no GUI / display needed)
python validate.py --qlim            # pass/fail table against the section-7 tolerances
python benchmark.py                  # A4 study: iterations, total time, time per iteration
python run_ieee14.py                 # console printout of the IEEE-14 solution
```

## Validation against a reference tool (do this once)
`validate.py` compares IEEE 14/30/57/118 with reference solutions in `reference/*.json`.
```
python -m pip install pypower
python make_reference.py            # writes reference/case<N>_q0.json and _q1.json (Q limits off / on)
python validate.py --qlim
```
Until those files exist, `validate.py` falls back to `independent_check.py` (a dense, separately written solver) for the
Q-limits-off cases and says so in the last column; that is a strong sanity check but is **not** the reference solution the
assignment asks for.  MATPOWER works too: export bus Vm/Va, branch PF/QF/PT/QT and loss in the same JSON format.

## Files
| File | Purpose |
|---|---|
| `network.py` | Bus / Branch / Generator / Network dataclasses, sparse `ybus()`, `check()` (islands, missing slack, bad data), ZIP/Z/PQ load models, tap on either side |
| `solver.py` | Polar NR, analytic sparse Jacobian, sparse LU, flat start, outer-loop PV↔PQ switching, results dict |
| `matpower_import.py`, `matpower_export.py`, `cdf_import.py` | case import / export (CDF reader is untested on real files) |
| `independent_check.py`, `validate.py`, `make_reference.py`, `benchmark.py` | verification and the A4 study |
| `gui_model.py`, `gui_fields.py` | everything the GUI does to the model (unit-tested, no Tk) |
| `gui_app.py`, `gui_widgets.py` | Tkinter view |
| `report.py`, `display.py`, `layout.py`, `units.py` | tables, CSV/XLSX/HTML/PDF export, colour rules, auto-layout, unit conversions |
| `AI_ERROR_LOG.md`, `GAP_ANALYSIS.md` | AI mistakes found / what was missing and what was changed |

## Solver conventions
Per unit on `Network.base_mva`; loads are positive consumption; generation positive.  Branch `b` is TOTAL charging.
`tap` / `shift_deg` follow the MATPOWER model on `tap_side` (`'from'` default, `'to'` supported).
`solve_power_flow(net, tolerance, max_iterations, enforce_q_limits=False, flat_start=True, initial_vm=None,
initial_va_deg=None, callback=None)` returns a dict and does **not** raise on non-convergence
(`converged=False`, `message`, `worst_buses`, `iteration_log`).  PV/slack voltages are always reset to their set-points.
Q limits are checked at converged points (outer loop), PV→PQ at the violated limit, PQ→PV when the voltage recovers; every
switch is logged and reported through `callback`.  Z/ZIP loads include their `dP/dV` term in the Jacobian.

## Known limits / still to do by you
* GUI: written to run on Tk 8.6 (Windows); it was exercised here only against a stand-in for Tk, so launch it and fix
  small display details.  Add what you find to `AI_ERROR_LOG.md`.
* IEEE CDF import follows the published format but has only been tested on a small hand-made sample.
* Edit `about_info.py` (name, roll number).  Commit module by module to Git.
* Loads are one aggregated load per bus (model PQ / Z / ZIP); generators are individual records (several per bus allowed).
