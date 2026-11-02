#!/usr/bin/env python
"""Create reference solutions with PYPOWER's runpf (same data, same options).  RUN ONCE.

    python -m pip install pypower
    python make_reference.py          ->  reference/case14_q0.json, case14_q1.json, ...

PYPOWER is used ONLY here, never inside the simulator.  (MATPOWER works too: run
runpf with mpoption('pf.alg','NR','pf.tol',1e-8,'pf.enforce_q_lims',q) and export the same
fields: bus Vm/Va, branch PF/QF/PT/QT in file order, and loss = sum(PF+PT).)
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from independent_check import read_raw          # only to parse the .m file

try:
    from pypower.api import runpf, ppoption
except Exception as exc:                         # pragma: no cover
    sys.exit(f"PYPOWER not available ({exc}). Install it with: python -m pip install pypower")

(HERE / "reference").mkdir(exist_ok=True)
for number in (14, 30, 57, 118):
    path = HERE / "data" / f"case{number}.m"
    base, bus, gen, branch = read_raw(path)
    for qlim in (0, 1):
        b = bus.copy()
        ref = b[:, 1] == 3
        b[:, 7] = 1.0                            # flat start (gen buses are reset to Vg by runpf)
        b[~ref, 8] = 0.0
        ppc = {"version": "2", "baseMVA": base, "bus": b, "gen": gen.copy(), "branch": branch.copy()}
        opt = ppoption(PF_ALG=1, PF_TOL=1e-8, PF_MAX_IT=30, ENFORCE_Q_LIMS=qlim, VERBOSE=0, OUT_ALL=0)
        res, ok = runpf(ppc, opt)
        if not ok:
            print(f"case{number} q{qlim}: reference did NOT converge")
            continue
        br = res["branch"]
        out = {"vm": res["bus"][:, 7].tolist(), "va_deg": res["bus"][:, 8].tolist(),
               "pf": br[:, 13].tolist(), "qf": br[:, 14].tolist(),
               "pt": br[:, 15].tolist(), "qt": br[:, 16].tolist(),
               "loss_mw": float(np.sum(br[:, 13] + br[:, 15]))}
        (HERE / "reference" / f"case{number}_q{qlim}.json").write_text(json.dumps(out))
        print("wrote", f"case{number}_q{qlim}.json")
