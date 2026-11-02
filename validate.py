#!/usr/bin/env python
"""Validate the NR engine on IEEE 14 / 30 / 57 / 118 against the acceptance criteria (section 7).

    python validate.py            Q limits off  (the acceptance table)
    python validate.py --qlim     also Q limits on

Reference solution used for each case:
  1. reference/<case>_q<0|1>.json  (made once by make_reference.py with PYPOWER/MATPOWER)  <- the real check
  2. otherwise, for Q limits off only: an independent dense solver (independent_check.py).
     That is a good sanity check but NOT the reference required by the assignment: the table says so.
Tolerances: mismatch 1e-8 pu | |dV| 1e-4 pu | |dtheta| 0.01 deg | branch P,Q 0.01 MW/MVAr
            | losses 0.1 % | IEEE-118 flat start, Q limits off: <= 7 iterations.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from matpower_import import load_matpower_case
from solver import solve_power_flow
from independent_check import solve_independent

CASES = [14, 30, 57, 118]
TOL = dict(mis=1e-8, vm=1e-4, va=0.01, flow=0.01, loss_rel=1e-3, it118=7)


def reference_for(number, qlim):
    ref_file = HERE / "reference" / f"case{number}_q{int(qlim)}.json"
    if ref_file.exists():
        return json.loads(ref_file.read_text()), "reference"
    if not qlim:
        return solve_independent(HERE / "data" / f"case{number}.m"), "independent"
    return None, "none"


def q_limit_consistency(net, res):
    """Q-limits-on sanity check when no reference exists: limits respected, back-switch rule satisfied."""
    ok = True
    by_bus = {b["bus"]: b for b in res["bus_results"]}
    pg, qg, qmin, qmax, vset, has = net.bus_generation()
    for i, b in enumerate(net.buses):
        r = by_bus[b.number]
        if b.kind.upper() == "PV" and has[i]:
            q = r["q_gen_mvar"] / net.base_mva
            if r["q_limit_hit"]:
                v_ok = (r["limit"] == "Qmax" and r["vm_pu"] <= vset[i] + 1e-6) or \
                       (r["limit"] == "Qmin" and r["vm_pu"] >= vset[i] - 1e-6)
                ok &= v_ok
            else:
                ok &= (qmin[i] - 1e-6 <= q <= qmax[i] + 1e-6) and abs(r["vm_pu"] - vset[i]) < 1e-8
    return ok


def check_case(number, qlim):
    net = load_matpower_case(HERE / "data" / f"case{number}.m")
    res = solve_power_flow(net, tolerance=1e-8, max_iterations=30, enforce_q_limits=qlim, flat_start=True)
    ref, source = reference_for(number, qlim)
    out = {"iters": res["iterations"], "source": source}
    checks = {"converged": res["converged"] and res["max_mismatch_pu"] <= TOL["mis"]}
    out["mis"] = res["max_mismatch_pu"]
    if ref is None:
        checks["Qlim consistency"] = q_limit_consistency(net, res)
        out.update(dvm=float("nan"), dva=float("nan"), dflow=float("nan"), dloss=float("nan"))
    else:
        vm = np.array([b["vm_pu"] for b in res["bus_results"]])
        va = np.array([b["va_deg"] for b in res["bus_results"]])
        out["dvm"] = float(np.abs(vm - ref["vm"]).max())
        out["dva"] = float(np.abs(va - ref["va_deg"]).max())
        brs = res["branch_results"]
        out["dflow"] = float(max(
            np.abs(np.array([b["p_from_mw"] for b in brs]) - ref["pf"]).max(),
            np.abs(np.array([b["q_from_mvar"] for b in brs]) - ref["qf"]).max(),
            np.abs(np.array([b["p_to_mw"] for b in brs]) - ref["pt"]).max(),
            np.abs(np.array([b["q_to_mvar"] for b in brs]) - ref["qt"]).max()))
        out["dloss"] = abs(res["total_p_loss_mw"] - ref["loss_mw"]) / max(abs(ref["loss_mw"]), 1e-12)
        checks.update({"V": out["dvm"] <= TOL["vm"], "angle": out["dva"] <= TOL["va"],
                       "flows": out["dflow"] <= TOL["flow"], "loss": out["dloss"] <= TOL["loss_rel"]})
    if number == 118 and not qlim:
        checks["iter<=7"] = res["iterations"] <= TOL["it118"]
    out["checks"], out["pass"] = checks, all(checks.values())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qlim", action="store_true", help="also validate with Q limits enforced")
    args = ap.parse_args()
    head = (f"{'case':8}{'Qlim':5}{'iter':>5}{'max mis':>10}{'dV pu':>10}{'dAng deg':>10}"
            f"{'dFlow':>10}{'dLoss %':>9}  {'against':13}result")
    print(head); print("-" * len(head))
    all_pass, all_reference = True, True
    for q in ([False, True] if args.qlim else [False]):
        for c in CASES:
            try:
                o = check_case(c, q)
            except Exception as exc:
                print(f"case{c:<4}{'on' if q else 'off':5} ERROR: {type(exc).__name__}: {exc}")
                all_pass = False
                continue
            bad = [k for k, v in o["checks"].items() if not v]
            all_reference &= o["source"] == "reference"
            print(f"case{c:<4}{'on' if q else 'off':5}{o['iters']:5d}{o['mis']:10.1e}{o['dvm']:10.1e}"
                  f"{o['dva']:10.1e}{o['dflow']:10.1e}{100 * o['dloss']:9.1e}  {o['source']:13}"
                  f"{'PASS' if o['pass'] else 'FAIL ' + ','.join(bad)}")
            all_pass &= o["pass"]
    print("-" * len(head))
    if all_pass and all_reference:
        print("OVERALL: PASS (against reference solutions)")
    elif all_pass:
        print("OVERALL: PASS against the independent solver only - run make_reference.py to compare with PYPOWER/MATPOWER")
    else:
        print("OVERALL: FAIL")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
