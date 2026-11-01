
from pathlib import Path
from time import perf_counter
import statistics

from matpower_import import load_matpower_case
from solver import solve_power_flow


DATA_DIR = Path(__file__).resolve().parent / "data"

CASE_NUMBERS = [14, 30, 57, 118]

TOLERANCE = 1e-8
MAX_ITERATIONS = 30
REPEATS = 7


def benchmark_case(case_number):
    case_path = DATA_DIR / f"case{case_number}.m"

    if not case_path.exists():
        raise FileNotFoundError(
            f"Cannot find {case_path}. Check the filename and extension."
        )

    times = []
    iteration_counts = []
    final_result = None

    # Each run gets a fresh network so previous solver runs
    # cannot affect the next run.
    for run in range(REPEATS):
        net = load_matpower_case(case_path)

        start = perf_counter()
        result = solve_power_flow(
            net,
            tolerance=TOLERANCE,
            max_iterations=MAX_ITERATIONS,
            enforce_q_limits=False,
        )
        elapsed = perf_counter() - start

        if not result["converged"]:
            raise RuntimeError(
                f"IEEE {case_number}-bus case did not converge "
                f"on run {run + 1}. "
                f"Mismatch = {result['max_mismatch_pu']:.3e} pu"
            )

        times.append(elapsed)
        iteration_counts.append(result["iterations"])
        final_result = result

    # Use medians to reduce the effect of timing fluctuations.
    median_time = statistics.median(times)
    median_iterations = statistics.median(iteration_counts)

    time_per_iteration = (
        median_time / median_iterations
        if median_iterations > 0
        else float("nan")
    )

    return {
        "case": case_number,
        "buses": len(final_result["bus_results"]),
        "iterations": median_iterations,
        "time_ms": median_time * 1000,
        "per_iteration_ms": time_per_iteration * 1000,
        "mismatch": final_result["max_mismatch_pu"],
        "loss_mw": final_result["total_p_loss_mw"],
    }


def main():
    print("Newton-Raphson Power Flow Benchmark")
    print(f"Tolerance: {TOLERANCE}")
    print(f"Maximum iterations: {MAX_ITERATIONS}")
    print(f"Reactive power limits: disabled")
    print(f"Timed runs per case: {REPEATS}")
    print("-" * 100)

    print(
        f"{'Case':<10}{'Buses':>8}{'Iterations':>13}"
        f"{'Total (ms)':>15}{'Per iter (ms)':>17}"
        f"{'Mismatch (pu)':>19}{'Loss (MW)':>13}"
    )

    for case_number in CASE_NUMBERS:
        row = benchmark_case(case_number)

        print(
            f"IEEE {row['case']:<5}"
            f"{row['buses']:>8}"
            f"{row['iterations']:>13}"
            f"{row['time_ms']:>15.4f}"
            f"{row['per_iteration_ms']:>17.4f}"
            f"{row['mismatch']:>19.3e}"
            f"{row['loss_mw']:>13.4f}"
        )


if __name__ == "__main__":
    main()
