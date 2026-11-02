"""Tests for the features added in the review: Z/ZIP Jacobian, restart, tap side, checks, export, ..."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from network import Bus, Branch, Generator, Network
from solver import solve_power_flow, calc_power, _jacobian, _newton
from matpower_import import load_matpower_case
from matpower_export import save_matpower_case
from cdf_import import load_cdf_case


def case(n=14):
    return load_matpower_case(ROOT / "data" / f"case{n}.m")


class TestVoltageDependentLoads(unittest.TestCase):
    def _net(self, model, **zip_):
        return Network(
            buses=[Bus(1, "SLACK", vm=1.03),
                   Bus(2, "PV", vm=1.01, p_gen=0.4, q_min=-1, q_max=1),
                   Bus(3, "PQ", p_load=0.7, q_load=0.3, load_model=model, **zip_),
                   Bus(4, "PQ", p_load=0.3, q_load=0.1, load_model=model, **zip_)],
            branches=[Branch(1, 2, 0.02, 0.2, 0.02), Branch(1, 3, 0.01, 0.15, 0.01),
                      Branch(2, 3, 0.03, 0.25, 0.02), Branch(3, 4, 0.02, 0.1)])

    def test_jacobian_includes_load_voltage_dependence(self):
        """Finite differences of the FULL mismatch (Pspec(V) - Pcalc) must equal -J_analytic."""
        for model, z in (("Z", {}), ("ZIP", dict(zip_z=0.3, zip_i=0.2, zip_p=0.5))):
            with self.subTest(model=model):
                net = self._net(model, **z)
                Y = net.ybus()
                vm = np.array([1.03, 1.01, 0.97, 0.95]); va = np.deg2rad([0, -2, -4, -5])
                ang, pq = np.array([1, 2, 3]), np.array([2, 3])
                P0 = np.array([b.p_load for b in net.buses]); Q0 = np.array([b.q_load for b in net.buses])
                pg = np.array([0, 0.4, 0, 0])

                def spec(vm_):
                    f = np.array([b.load_factor(v)[0] for b, v in zip(net.buses, vm_)])
                    return pg - P0 * f, 0 - Q0 * f

                def F(x):
                    v, a = vm.copy(), va.copy()
                    a[ang] = x[:3]; v[pq] = x[3:]
                    ps, qs = spec(v); pc, qc = calc_power(Y, v, a)
                    return np.r_[(ps - pc)[ang], (qs - qc)[pq]]

                x0 = np.r_[va[ang], vm[pq]]
                df = np.array([b.load_factor(v)[1] for b, v in zip(net.buses, vm)])
                pc, qc = calc_power(Y, vm, va)
                J = _jacobian(Y, vm, va, pc, qc, ang, pq, dpspec_dvm=-P0 * df, dqspec_dvm=-Q0 * df).toarray()
                h, Jfd = 1e-7, np.zeros((5, 5))
                for k in range(5):
                    e = np.zeros(5); e[k] = h
                    Jfd[:, k] = (F(x0 + e) - F(x0 - e)) / (2 * h)
                np.testing.assert_allclose(-J, Jfd, atol=1e-6)

    def test_z_load_converges_quadratically(self):
        net = self._net("Z")
        res = solve_power_flow(net, tolerance=1e-12)
        h = [e["max_mismatch_pu"] for e in res["iteration_log"]]
        self.assertTrue(res["converged"]); self.assertLessEqual(res["iterations"], 6)
        self.assertLess(h[-1] / h[-2], 1e-1)                  # strongly super-linear last step
        by = {b["bus"]: b for b in res["bus_results"]}
        self.assertAlmostEqual(by[3]["p_load_mw"], 70.0 * by[3]["vm_pu"] ** 2, places=7)

    def test_zip_pure_p_equals_pq(self):
        a = solve_power_flow(self._net("PQ"))
        b = solve_power_flow(self._net("ZIP", zip_z=0.0, zip_i=0.0, zip_p=1.0))
        np.testing.assert_allclose([x["vm_pu"] for x in a["bus_results"]], [x["vm_pu"] for x in b["bus_results"]], atol=1e-12)

    def test_zip_shares_must_sum_to_one(self):
        net = self._net("ZIP", zip_z=0.5, zip_i=0.5, zip_p=0.5)
        self.assertTrue(any("ZIP" in i.message for i in net.errors()))
        with self.assertRaises(ValueError):
            solve_power_flow(net)


class TestStartAndSetpoints(unittest.TestCase):
    def test_wrong_pv_voltage_in_initial_vector_is_overridden(self):
        net = case(14)
        ref = solve_power_flow(net)
        bad = [0.9] * len(net.buses)
        res = solve_power_flow(net, flat_start=False, initial_vm=bad, initial_va_deg=[0.0] * 14)
        self.assertTrue(res["converged"])
        np.testing.assert_allclose([b["vm_pu"] for b in res["bus_results"]],
                                   [b["vm_pu"] for b in ref["bus_results"]], atol=1e-9)

    def test_restart_from_previous_solution_converges_immediately(self):
        net = case(30)
        first = solve_power_flow(net)
        again = solve_power_flow(net, flat_start=False, initial_vm=first["vm"], initial_va_deg=first["va_deg"])
        self.assertEqual(again["iterations"], 0)

    def test_restart_after_q_limit_run_keeps_pv_setpoints(self):
        net = case(118)
        first = solve_power_flow(net, enforce_q_limits=True)
        again = solve_power_flow(net, flat_start=False, initial_vm=first["vm"], initial_va_deg=first["va_deg"],
                                 enforce_q_limits=False)
        pg, qg, qmin, qmax, vset, has = net.bus_generation()
        for i, b in enumerate(net.buses):
            if b.kind in ("PV", "SLACK") and has[i]:
                self.assertAlmostEqual(again["bus_results"][i]["vm_pu"], vset[i], places=9)

    def test_non_convergence_returns_result_not_exception(self):
        net = case(14)
        for b in net.buses:
            b.p_load *= 40; b.q_load *= 40
        res = solve_power_flow(net)
        self.assertFalse(res["converged"])
        self.assertTrue(res["worst_buses"])
        self.assertGreater(res["max_mismatch_pu"], 1e-8)
        self.assertTrue(res["message"])

    def test_callback_receives_iterations_and_switches(self):
        net = case(14)
        net.generators[1].q_max = 0.30            # bus 2 needs ~0.44 pu
        lines = []
        res = solve_power_flow(net, enforce_q_limits=True, callback=lines.append)
        self.assertTrue(any("iteration" in l for l in lines))
        self.assertTrue(any("SWITCH bus 2" in l for l in lines))
        self.assertTrue(res["converged"])


class TestBranchModel(unittest.TestCase):
    def test_tap_on_to_side_equals_reversed_branch(self):
        a = case(14)
        b = copy.deepcopy(a)
        br = b.branches[7]                      # 4-7, tap 0.978
        br.from_bus, br.to_bus, br.tap_side = br.to_bus, br.from_bus, "to"
        ra, rb = solve_power_flow(a), solve_power_flow(b)
        np.testing.assert_allclose([x["vm_pu"] for x in ra["bus_results"]], [x["vm_pu"] for x in rb["bus_results"]], atol=1e-10)
        self.assertAlmostEqual(ra["branch_results"][7]["p_from_mw"], rb["branch_results"][7]["p_to_mw"], places=8)

    def test_flows_use_same_model_as_ybus_with_phase_shift(self):
        net = case(14)
        net.branches[0].shift_deg = 4.0
        net.branches[5].tap, net.branches[5].tap_side = 1.04, "to"
        res = solve_power_flow(net)
        Y = net.ybus().toarray()
        vm = np.array([b["vm_pu"] for b in res["bus_results"]]); va = np.deg2rad([b["va_deg"] for b in res["bus_results"]])
        V = vm * np.exp(1j * va)
        S = V * np.conj(Y @ V)
        # sum of branch (from+to) flows plus shunts must reproduce the bus injections
        inj = np.zeros(14, complex)
        for r, br in zip(res["branch_results"], net.branches):
            inj[net.index[br.from_bus]] += complex(r["p_from_mw"], r["q_from_mvar"]) / net.base_mva
            inj[net.index[br.to_bus]] += complex(r["p_to_mw"], r["q_to_mvar"]) / net.base_mva
        ysh = np.array([complex(b.g_shunt, b.b_shunt) for b in net.buses])
        inj += np.abs(V) ** 2 * np.conj(ysh)
        np.testing.assert_allclose(inj, S, atol=1e-9)

    def test_i2r_loss_equals_series_loss(self):
        net = case(14)
        res = solve_power_flow(net)
        vm = np.array([b["vm_pu"] for b in res["bus_results"]]); va = np.deg2rad([b["va_deg"] for b in res["bus_results"]])
        V = vm * np.exp(1j * va)
        for k, br in enumerate(net.branches):
            i, j = net.index[br.from_bus], net.index[br.to_bus]
            a = br.tap
            Is = (V[i] / a - V[j]) / complex(br.r, br.x)
            self.assertAlmostEqual(res["branch_results"][k]["p_loss_mw"], abs(Is) ** 2 * br.r * 100, places=7)

    def test_out_of_service_branch_stays_in_results_and_matches_removal(self):
        a, b = case(14), case(14)
        a.branches[9].in_service = False
        b.branches.pop(9)
        ra, rb = solve_power_flow(a), solve_power_flow(b)
        self.assertEqual(len(ra["branch_results"]), 20)
        self.assertFalse(ra["branch_results"][9]["active"])
        np.testing.assert_allclose([x["vm_pu"] for x in ra["bus_results"]], [x["vm_pu"] for x in rb["bus_results"]], atol=1e-12)


class TestGenerators(unittest.TestCase):
    def test_multiple_generators_at_one_bus(self):
        net = case(14)
        base = solve_power_flow(net)
        net.generators.append(Generator(2, p_gen=0.10, q_min=-0.2, q_max=0.3, v_setpoint=1.045, name="second unit"))
        net.generators[1].p_gen = 0.30             # total P at bus 2 stays 0.40 pu
        res = solve_power_flow(net)
        self.assertTrue(res["converged"])
        units = [g for g in res["generator_results"] if g["bus"] == 2]
        self.assertEqual(len(units), 2)
        self.assertAlmostEqual(sum(u["p_gen_mw"] for u in units), 40.0, places=6)
        self.assertAlmostEqual(sum(u["q_gen_mvar"] for u in units),
                               next(g for g in base["generator_results"] if g["bus"] == 2)["q_gen_mvar"], delta=0.2)

    def test_generator_out_of_service_turns_pv_bus_into_pq(self):
        net = case(14)
        net.generators[3].in_service = False       # bus 6
        res = solve_power_flow(net)
        self.assertTrue(res["converged"])
        self.assertTrue(any("PV bus without" in i.message for i in net.check()))
        self.assertNotEqual(next(b for b in res["bus_results"] if b["bus"] == 6)["vm_pu"], 1.07)


class TestChecksAndIO(unittest.TestCase):
    def test_island_without_slack_and_dangling(self):
        net = case(14)
        net.buses.append(Bus(99)); net.buses.append(Bus(98)); net.rebuild_index()
        net.branches.append(Branch(98, 99, 0.01, 0.1))
        msgs = " | ".join(i.message for i in net.errors())
        self.assertIn("island without a slack", msgs)
        # deleting a bus but forgetting its branches leaves dangling branches
        net2 = case(14)
        net2.buses = [b for b in net2.buses if b.number != 14]
        self.assertTrue(any("dangling" in i.message for i in net2.errors()))

    def test_bad_data_flagged(self):
        net = case(14)
        net.branches[0].rate_mva = -5; net.branches[1].r = net.branches[1].x = 0.0; net.branches[2].tap = 0
        msgs = " | ".join(i.message for i in net.errors())
        for text in ("negative rating", "zero series impedance", "tap ratio"):
            self.assertIn(text, msgs)
        with self.assertRaises(ValueError):
            solve_power_flow(net)

    def test_missing_slack(self):
        net = case(14); net.buses[0].kind = "PQ"
        self.assertTrue(any("slack" in i.message for i in net.errors()))

    def test_matpower_export_roundtrip(self):
        for n in (14, 57):
            net = case(n)
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "x.m"
                save_matpower_case(net, p)
                net2 = load_matpower_case(p)
            r1, r2 = solve_power_flow(net), solve_power_flow(net2)
            np.testing.assert_allclose([b["vm_pu"] for b in r1["bus_results"]], [b["vm_pu"] for b in r2["bus_results"]], atol=1e-9)
            self.assertAlmostEqual(r1["total_p_loss_mw"], r2["total_p_loss_mw"], places=6)

    SAMPLE_CDF = """ 08/19/93 UW ARCHIVE           100.0  1962 W IEEE 3 Bus Test Case
BUS DATA FOLLOWS                             3 ITEMS
   1 Bus 1     HV  1  1  3 1.060   0.0      0.0      0.0    232.4   -16.9     0.0 1.060     0.0     0.0   0.0    0.0        0
   2 Bus 2     HV  1  1  2 1.045  -4.98    21.7     12.7     40.0    42.4     0.0 1.045    50.0   -40.0   0.0    0.0        0
   3 Bus 3     HV  1  1  0 1.010 -12.72    94.2     19.0      0.0     0.0     0.0 0.0       0.0     0.0   0.0    0.19       0
-999
BRANCH DATA FOLLOWS                          3 ITEMS
   1    2  1  1 1 0  0.01938   0.05917     0.0528     0     0     0    0 0  0.0       0.0 0.0  0.0   0.0  0.0  0.0
   2    3  1  1 1 0  0.04699   0.19797     0.0438     0     0     0    0 0  0.0       0.0 0.0  0.0   0.0  0.0  0.0
   1    3  1  1 1 1  0.0       0.20912     0.0        0     0     0    0 0  0.978     0.0 0.0  0.0   0.0  0.0  0.0
-999
END OF DATA
"""

    def test_cdf_import(self):
        net = load_cdf_case(self.SAMPLE_CDF)
        self.assertEqual((len(net.buses), len(net.branches)), (3, 3))
        self.assertEqual(net.buses[0].kind, "SLACK")
        self.assertAlmostEqual(net.branches[2].tap, 0.978)
        self.assertAlmostEqual(net.buses[2].b_shunt, 0.19)
        self.assertTrue(solve_power_flow(net)["converged"])


if __name__ == "__main__":
    unittest.main()
