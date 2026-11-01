"""Run repeatable internal validation on the bundled IEEE MATPOWER cases.

This script checks whether each case imports and whether the project's own
Newton-Raphson solver converges. It is not an independent MATPOWER comparison.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from matpower_import import load_matpower_case
from solver import solve_power_flow

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DEFAULT_CASES = (14, 30, 57, 118)


def run_case(case_number: int, enforce_q_limits: bool) -> dict:
    case_path = DATA_DIR / f"case{case_number}.m"
    if not case_path.is_file():
        raise FileNotFoundError(f"Case file not found: {case_path}")

    network = load_matpower_case(case_path)
    result = solve_power_flow(
        network,
        tolerance=1e-8,
        max_iterations=30,
        enforce_q_limits=enforce_q_limits,
    )

    if not result.get("converged", False):
        raise RuntimeError(
            f"IEEE {case_number}: solver did not converge; "
            f"maximum mismatch = {result.get('max_mismatch_pu')} pu"
        )

    bus_results = result.get("bus_results", [])
    generator_results = result.get("generator_results", [])
    switches = result.get("q_limit_switches", [])

    return {
        "case": f"IEEE {case_number}",
        "case_number": case_number,
        "buses": len(network.buses),
        "branches": len(network.branches),
        "base_mva": network.base_mva,
        "q_limits_enforced": enforce_q_limits,
        "converged": bool(result["converged"]),
        "iterations": int(result["iterations"]),
        "max_mismatch_pu": float(result["max_mismatch_pu"]),
        "active_loss_mw": float(result["total_p_loss_mw"]),
        "reactive_loss_mvar": float(result["total_q_loss_mvar"]),
        "pv_to_pq_or_other_q_switches": len(switches),
        "switches": switches,
        "bus_results": bus_results,
        "generator_results": generator_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate bundled IEEE cases using this project's solver."
    )
    parser.add_argument(
        "--cases", nargs="+", type=int, default=list(DEFAULT_CASES),
        choices=(14, 30, 57, 118),
        help="IEEE case sizes to run (default: 14 30 57 118).",
    )
    parser.add_argument(
        "--q-limits", action="store_true",
        help="Enable the solver's implemented PV-bus Q-limit logic.",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "validation_results",
        help="Output filename prefix (default: validation_results).",
    )
    args = parser.parse_args()

    rows = []
    failures = []
    for case_number in args.cases:
        try:
            rows.append(run_case(case_number, args.q_limits))
        except Exception as exc:  # report all cases instead of stopping at first
            failures.append({"case": f"IEEE {case_number}", "error": str(exc)})

    print("E4234 internal power-flow validation")
    print(f"Q-limit enforcement: {'enabled' if args.q_limits else 'disabled'}")
    print("These results are from the project solver, not an independent reference.")
    print("-" * 100)
    print(f"{'Case':<12}{'Buses':>8}{'Branches':>10}{'Iter':>8}{'Mismatch (pu)':>20}{'Loss (MW)':>14}")
    for row in rows:
        print(
            f"{row['case']:<12}{row['buses']:>8}{row['branches']:>10}"
            f"{row['iterations']:>8}{row['max_mismatch_pu']:>20.3e}"
            f"{row['active_loss_mw']:>14.4f}"
        )
    for failure in failures:
        print(f"FAILED {failure['case']}: {failure['error']}")

    # Keep detailed bus/generator outputs in JSON for later comparison with MATPOWER.
    output_prefix = args.output
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "solver": "E4234 project solver",
        "independent_reference_comparison": False,
        "q_limits_enforced": args.q_limits,
        "results": rows,
        "failures": failures,
    }
    json_path = output_prefix.with_suffix(".json")
    csv_path = output_prefix.with_suffix(".csv")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "case", "buses", "branches", "base_mva", "q_limits_enforced",
            "converged", "iterations", "max_mismatch_pu",
            "active_loss_mw", "reactive_loss_mvar",
            "pv_to_pq_or_other_q_switches",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in fields})

    print(f"\nDetailed results: {json_path}")
    print(f"Summary CSV:      {csv_path}")
    print("NOTE: Q-limit handling for a slack generator/reference-bus reassignment")
    print("must be independently verified before claiming full Q-limit compliance.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
