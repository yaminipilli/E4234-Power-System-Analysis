
from matpower_import import load_matpower_case
from solver import solve_power_flow

net = load_matpower_case("data/case14.m")

print("Buses:", len(net.buses))
print("Branches:", len(net.branches))
print("Base MVA:", net.base_mva)

result = solve_power_flow(
    net,
    tolerance=1e-8,
    max_iterations=30,
    enforce_q_limits=False,
)

print("\nConverged:", result["converged"])
print("Iterations:", result["iterations"])
print("Maximum mismatch (pu):", result["max_mismatch_pu"])
print("Total active loss (MW):", result["total_p_loss_mw"])
print("Total reactive loss (MVAr):", result["total_q_loss_mvar"])

# Bus results
print("\nBus results")
for bus in result["bus_results"]:
    print(
        f"Bus {bus['bus']:2d}  "
        f"Type={bus['type']:5s}  "
        f"V={bus['vm_pu']:.6f} pu  "
        f"Angle={bus['va_deg']:.4f} deg  "
        f"P={bus['p_injection_mw']:.4f} MW  "
        f"Q={bus['q_injection_mvar']:.4f} MVAr"
    )

# Individual generator results
print("\nGenerator results")
print("-" * 76)
print(
    f"{'Bus':>4}  {'Generator':<30} "
    f"{'Pg (MW)':>12} {'Qg (MVAr)':>14}"
)

for gen in result["generator_results"]:
    print(
        f"{gen['bus']:4d}  "
        f"{gen['name']:<30.30} "
        f"{gen['p_gen_mw']:12.4f} "
        f"{gen['q_gen_mvar']:14.4f}"
    )

# Branch results
print("\nBranch results")
print("-" * 100)
print(
    f"{'From':>5} {'To':>5} "
    f"{'P_from (MW)':>14} {'Q_from (MVAr)':>15} "
    f"{'P_to (MW)':>13} {'Q_to (MVAr)':>14} "
    f"{'Loading (%)':>12}"
)

for br in result["branch_results"]:
    loading = (
        f"{br['loading_percent']:.2f}"
        if br["loading_percent"] is not None
        else "N/A"
    )
    print(
        f"{br['from_bus']:5d} "
        f"{br['to_bus']:5d} "
        f"{br['p_from_mw']:14.4f} "
        f"{br['q_from_mvar']:15.4f} "
        f"{br['p_to_mw']:13.4f} "
        f"{br['q_to_mvar']:14.4f} "
        f"{loading:>12}"
    )

print("\nSummary")
print("-" * 40)
print(f"Converged              : {result['converged']}")
print(f"Newton-Raphson iterations: {result['iterations']}")
print(f"Final mismatch (pu)    : {result['max_mismatch_pu']:.3e}")
print(f"Total active loss (MW) : {result['total_p_loss_mw']:.4f}")
print(f"Total reactive loss (MVAr): {result['total_q_loss_mvar']:.4f}")
