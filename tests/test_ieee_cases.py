"""IEEE 14 / 30 / 57 / 118 acceptance-style tests (assignment section 7), using an independent solver."""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matpower_import import load_matpower_case
from solver import solve_power_flow
from independent_check import solve_independent

CASES = [14, 30, 57, 118]


def run(number, **kw):
    net = load_matpower_case(ROOT / "data" / f"case{number}.m")
    return net, solve_power_flow(net, tolerance=1e-8, max_iterations=30, **kw)


class TestIEEECases(unittest.TestCase):
    def test_flat_start_matches_independent_solver_on_all_cases(self):
        for c in CASES:
            with self.subTest(case=c):
                net, res = run(c, enforce_q_limits=False, flat_start=True)
                ref = solve_independent(ROOT / "data" / f"case{c}.m")
                self.assertTrue(res["converged"])
                self.assertLessEqual(res["max_mismatch_pu"], 1e-8)
                vm = np.array([b["vm_pu"] for b in res["bus_results"]])
                va = np.array([b["va_deg"] for b in res["bus_results"]])
                self.assertLessEqual(np.abs(vm - ref["vm"]).max(), 1e-4)
                self.assertLessEqual(np.abs(va - ref["va_deg"]).max(), 0.01)
                for key, ours in (("pf", "p_from_mw"), ("qf", "q_from_mvar"), ("pt", "p_to_mw"), ("qt", "q_to_mvar")):
                    got = np.array([b[ours] for b in res["branch_results"]])
                    self.assertLessEqual(np.abs(got - ref[key]).max(), 0.01, key)
                self.assertLessEqual(abs(res["total_p_loss_mw"] - ref["loss_mw"]) / ref["loss_mw"], 1e-3)

    def test_ieee118_needs_at_most_7_iterations_from_flat_start(self):
        _, res = run(118, enforce_q_limits=False, flat_start=True)
        self.assertLessEqual(res["iterations"], 7)

    def test_flat_start_is_really_flat(self):
        """Starting from the stored (solved) voltages would hide convergence problems."""
        net, flat = run(118, enforce_q_limits=False, flat_start=True)
        _, stored = run(118, enforce_q_limits=False, flat_start=False)
        self.assertLess(stored["iterations"], flat["iterations"])
        self.assertEqual(flat["iteration_log"][0]["iteration"], 0)
        self.assertGreater(flat["iteration_log"][0]["max_mismatch_pu"], 0.1)      # large initial mismatch

    def test_published_ieee14_values(self):
        _, res = run(14)
        V = [1.060, 1.045, 1.010, 1.018, 1.020, 1.070, 1.062, 1.090, 1.056, 1.051, 1.057, 1.055, 1.050, 1.036]
        A = [0, -4.98, -12.72, -10.33, -8.78, -14.22, -13.37, -13.36, -14.94, -15.10, -14.79, -15.07, -15.16, -16.04]
        np.testing.assert_allclose([b["vm_pu"] for b in res["bus_results"]], V, atol=1e-3)
        np.testing.assert_allclose([b["va_deg"] for b in res["bus_results"]], A, atol=0.02)
        self.assertAlmostEqual(res["total_p_loss_mw"], 13.393, delta=0.005)
        slack = next(g for g in res["generator_results"] if g["bus"] == 1)
        self.assertAlmostEqual(slack["p_gen_mw"], 232.39, delta=0.01)

    def test_power_balance_all_cases(self):
        for c in CASES:
            with self.subTest(case=c):
                net, res = run(c)
                gen = sum(g["p_gen_mw"] for g in res["generator_results"])
                load = sum(b["p_load_mw"] for b in res["bus_results"])
                shunt = sum(b["p_shunt_mw"] for b in res["bus_results"])
                self.assertAlmostEqual(gen - load - shunt, res["total_p_loss_mw"], places=5)
                self.assertAlmostEqual(sum(b["p_gen_mw"] - b["p_load_mw"] - b["p_shunt_mw"]
                                           for b in res["bus_results"]), res["total_p_loss_mw"], places=5)

    def test_q_limits_postconditions_all_cases(self):
        """With limits enforced: PV units inside limits, held buses on the correct side of V set-point."""
        for c in CASES:
            with self.subTest(case=c):
                net, res = run(c, enforce_q_limits=True)
                self.assertTrue(res["converged"])
                pg, qg, qmin, qmax, vset, has = net.bus_generation()
                by = {b["bus"]: b for b in res["bus_results"]}
                for i, b in enumerate(net.buses):
                    r = by[b.number]
                    if b.kind == "PV" and has[i]:
                        if r["q_limit_hit"]:
                            self.assertAlmostEqual(r["q_gen_mvar"] / net.base_mva,
                                                   qmax[i] if r["limit"] == "Qmax" else qmin[i], places=6)
                            if r["limit"] == "Qmax":
                                self.assertLessEqual(r["vm_pu"], vset[i] + 1e-6)
                            else:
                                self.assertGreaterEqual(r["vm_pu"], vset[i] - 1e-6)
                        else:
                            self.assertAlmostEqual(r["vm_pu"], vset[i], places=8)
                            self.assertGreaterEqual(r["q_gen_mvar"] / net.base_mva, qmin[i] - 1e-6)
                            self.assertLessEqual(r["q_gen_mvar"] / net.base_mva, qmax[i] + 1e-6)

    def test_slack_reactive_output_is_not_clipped(self):
        """IEEE-14 slack needs about -16.5 MVAr although its generator limits are 0..10 MVAr."""
        _, res = run(14, enforce_q_limits=True)
        slack = next(g for g in res["generator_results"] if g["bus"] == 1)
        self.assertAlmostEqual(slack["q_gen_mvar"], -16.55, delta=0.05)


if __name__ == "__main__":
    unittest.main()
