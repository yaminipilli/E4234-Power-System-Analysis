#!/usr/bin/env python
"""
Validate the Newton-Raphson load-flow engine against independent PYPOWER
reference solutions for IEEE 14, 30, 57 and 118 bus systems.

Run:
    python validate.py
        Automatically creates missing Q-limit-OFF references, then validates
        all four IEEE cases.

    python validate.py --qlim
        Automatically creates missing references and validates Q limits OFF
        and ON.

    python validate.py --timing
        Also displays total solve time and time per NR iteration.

    python validate.py --qlim --timing
        Validates both Q-limit settings and displays timing.

Reference files are generated using PYPOWER runpf. PYPOWER is used only for
independent reference generation; it is never used by the simulator's
solve_power_flow() implementation.

Existing reference files are reused. Only missing reference files are created.
Delete the reference folder if you deliberately want to regenerate all files.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from matpower_import import load_matpower_case
from solver import solve_power_flow
from independent_check import read_raw


CASES = [14, 30, 57, 118]

TOL = {
    "mis": 1e-8,
    "vm": 1e-4,
    "va": 0.01,
    "flow": 0.01,
    "loss_rel": 1e-3,
    "it118": 7,
}


def reference_path(number, qlim):
    return HERE / "reference" / f"case{number}_q{int(qlim)}.json"


def generate_reference(number, qlim):
    """Generate one independent reference solution using PYPOWER."""
    try:
        from pypower.api import runpf, ppoption
    except Exception as exc:
        raise RuntimeError(
            f"PYPOWER is not available ({exc}). Install it using:\n"
            "python -m pip install pypower"
        ) from exc

    case_path = HERE / "data" / f"case{number}.m"
    if not case_path.exists():
        raise FileNotFoundError(f"MATPOWER case file not found: {case_path}")

    base, bus, gen, branch = read_raw(case_path)

    # Match the flat-start convention used by the validation run.
    b = bus.copy()
    ref_bus = b[:, 1] == 3
    b[:, 7] = 1.0
    b[~ref_bus, 8] = 0.0

    ppc = {
        "version": "2",
        "baseMVA": base,
        "bus": b,
        "gen": gen.copy(),
        "branch": branch.copy(),
    }

    options = ppoption(
        PF_ALG=1,
        PF_TOL=1e-8,
        PF_MAX_IT=30,
        ENFORCE_Q_LIMS=int(qlim),
        VERBOSE=0,
        OUT_ALL=0,
    )

    result, success = runpf(ppc, options)

    if not success:
        raise RuntimeError(
            f"PYPOWER reference did not converge for IEEE {number}, "
            f"Q limits {'ON' if qlim else 'OFF'}."
        )

    br = result["branch"]

    reference = {
        "vm": result["bus"][:, 7].tolist(),
        "va_deg": result["bus"][:, 8].tolist(),
        "pf": br[:, 13].tolist(),
        "qf": br[:, 14].tolist(),
        "pt": br[:, 15].tolist(),
        "qt": br[:, 16].tolist(),
        "loss_mw": float(np.sum(br[:, 13] + br[:, 15])),
    }

    output_path = reference_path(number, qlim)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(reference, indent=2))

    print(f"  Created {output_path.relative_to(HERE)}")


def ensure_references(qlim_values):
    """
    Automatically generate missing references.
    Existing reference JSON files are reused.
    """
    missing = [
        (number, qlim)
        for qlim in qlim_values
        for number in CASES
        if not reference_path(number, qlim).exists()
    ]

    if not missing:
        print("Reference files found. Reusing existing PYPOWER references.\n")
        return

    print("Generating missing PYPOWER reference solutions...\n")

    for number, qlim in missing:
        generate_reference(number, qlim)

    print("\nReference generation completed.\n")


def load_reference(number, qlim):
    """Load the PYPOWER reference JSON for one case."""
    path = reference_path(number, qlim)

    if not path.exists():
        raise FileNotFoundError(
            f"Reference file is missing: {path}\n"
            "Check that PYPOWER reference generation completed successfully."
        )

    return json.loads(path.read_text())


def check_case(number, qlim):
    """Run the student's NR solver and compare its result with PYPOWER."""
    case_path = HERE / "data" / f"case{number}.m"
    net = load_matpower_case(case_path)
    ref = load_reference(number, qlim)

    start = time.perf_counter()

    result = solve_power_flow(
        net,
        tolerance=1e-8,
        max_iterations=30,
        enforce_q_limits=qlim,
        flat_start=True,
    )

    elapsed = time.perf_counter() - start
    iterations = result["iterations"]

    out = {
        "iterations": iterations,
        "time_s": elapsed,
        "time_per_iteration_s": (
            elapsed / iterations if iterations > 0 else float("nan")
        ),
        "mis": result["max_mismatch_pu"],
    }

    checks = {
        "converged": (
            result["converged"]
            and result["max_mismatch_pu"] <= TOL["mis"]
        )
    }

    # Bus voltage magnitude and angle comparisons.
    vm = np.array([b["vm_pu"] for b in result["bus_results"]])
    va = np.array([b["va_deg"] for b in result["bus_results"]])

    out["dvm"] = float(np.max(np.abs(vm - np.asarray(ref["vm"]))))
    out["dva"] = float(
        np.max(np.abs(va - np.asarray(ref["va_deg"])))
    )

    # Branch results are compared in MATPOWER input-file order.
    branch_results = result["branch_results"]

    flow_errors = [
        np.abs(
            np.array([b["p_from_mw"] for b in branch_results])
            - np.asarray(ref["pf"])
        ).max(),
        np.abs(
            np.array([b["q_from_mvar"] for b in branch_results])
            - np.asarray(ref["qf"])
        ).max(),
        np.abs(
            np.array([b["p_to_mw"] for b in branch_results])
            - np.asarray(ref["pt"])
        ).max(),
        np.abs(
            np.array([b["q_to_mvar"] for b in branch_results])
            - np.asarray(ref["qt"])
        ).max(),
    ]

    out["dflow"] = float(max(flow_errors))

    # Relative total active-power loss difference.
    reference_loss = float(ref["loss_mw"])
    out["dloss"] = (
        abs(result["total_p_loss_mw"] - reference_loss)
        / max(abs(reference_loss), 1e-12)
    )

    checks.update({
        "voltage": out["dvm"] <= TOL["vm"],
        "angle": out["dva"] <= TOL["va"],
        "branch_flows": out["dflow"] <= TOL["flow"],
        "total_losses": out["dloss"] <= TOL["loss_rel"],
    })

    # Assignment-specific IEEE 118 iteration requirement:
    # flat start, Q limits OFF, no more than 7 NR iterations.
    if number == 118 and not qlim:
        checks["IEEE118_iterations"] = (
            iterations <= TOL["it118"]
        )

    out["checks"] = checks
    out["pass"] = all(checks.values())

    return out


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Validate the NR load-flow solver against IEEE PYPOWER references."
        )
    )

    parser.add_argument(
        "--qlim",
        action="store_true",
        help="Validate with Q limits OFF and ON.",
    )

    parser.add_argument(
        "--timing",
        action="store_true",
        help="Display total solve time and time per NR iteration.",
    )

    args = parser.parse_args()

    qlim_values = (0, 1) if args.qlim else (0,)

    # Automatically create any missing reference files before validation.
    try:
        ensure_references(qlim_values)
    except Exception as exc:
        print(f"\nREFERENCE GENERATION ERROR: {type(exc).__name__}: {exc}")
        return 1

    header = (
        f"{'Case':<8}{'Qlim':<6}{'Iter':>5}{'Max mismatch':>15}"
        f"{'dV pu':>12}{'dAng deg':>12}{'dFlow':>12}{'dLoss %':>11}"
    )

    if args.timing:
        header += f"{'Time ms':>11}{'ms/iter':>11}"

    header += "  Result"

    print(header)
    print("-" * len(header))

    all_pass = True

    for qlim in qlim_values:
        for number in CASES:
            try:
                out = check_case(number, bool(qlim))

                passed = out["pass"]
                all_pass = all_pass and passed

                line = (
                    f"{number:<8}{'ON' if qlim else 'OFF':<6}"
                    f"{out['iterations']:>5d}"
                    f"{out['mis']:>15.2e}"
                    f"{out['dvm']:>12.2e}"
                    f"{out['dva']:>12.2e}"
                    f"{out['dflow']:>12.2e}"
                    f"{100.0 * out['dloss']:>11.3e}"
                )

                if args.timing:
                    line += (
                        f"{1000.0 * out['time_s']:>11.3f}"
                        f"{1000.0 * out['time_per_iteration_s']:>11.3f}"
                    )

                line += "  " + ("PASS" if passed else "FAIL")
                print(line)

                failed_checks = [
                    name
                    for name, passed_check in out["checks"].items()
                    if not passed_check
                ]

                if failed_checks:
                    print(
                        "        Failed checks: "
                        + ", ".join(failed_checks)
                    )

            except Exception as exc:
                all_pass = False
                print(
                    f"{number:<8}{'ON' if qlim else 'OFF':<6}"
                    f"  ERROR: {type(exc).__name__}: {exc}"
                )

    print("-" * len(header))

    if all_pass:
        print("OVERALL: PASS (all tested cases matched PYPOWER references)")
        return 0

    print("OVERALL: FAIL (see failed checks or errors above)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
