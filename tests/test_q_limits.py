
import unittest

from network import Bus, Branch, Network
from solver import solve_power_flow


class TestReactivePowerLimits(unittest.TestCase):

    def test_pv_bus_switches_to_pq_at_qmax(self):
        net = Network(
            buses=[
                Bus(1, "SLACK", vm=1.04),
                Bus(
                    2, "PV",
                    vm=1.02,
                    p_gen=0.50,
                    q_min=-0.05,
                    q_max=0.05,
                ),
                Bus(
                    3, "PQ",
                    p_load=0.80,
                    q_load=0.60,
                ),
            ],
            branches=[
                Branch(1, 2, r=0.02, x=0.20, b=0.02),
                Branch(1, 3, r=0.01, x=0.15, b=0.01),
                Branch(2, 3, r=0.03, x=0.25, b=0.02),
            ],
        )

        result = solve_power_flow(
            net,
            tolerance=1e-8,
            max_iterations=30,
            enforce_q_limits=True,
        )

        self.assertTrue(result["converged"])
        self.assertLessEqual(result["max_mismatch_pu"], 1e-8)

        switches = result["q_limit_switches"]
        self.assertTrue(
            any(
                s["bus"] == 2
                and s["from"] == "PV"
                and s["to"] == "PQ"
                for s in switches
            )
        )

        bus2 = next(
            b for b in result["bus_results"]
            if b["bus"] == 2
        )

        self.assertTrue(bus2["q_limit_hit"])
        self.assertAlmostEqual(
            bus2["q_injection_pu"], 0.05, places=6
        )

    def test_q_limit_switches_are_recorded_consistently(self):
        net = Network(
            buses=[
                Bus(1, "SLACK", vm=1.04),
                Bus(
                    2, "PV",
                    vm=1.02,
                    p_gen=0.50,
                    q_min=-0.05,
                    q_max=0.05,
                ),
                Bus(3, "PQ", p_load=0.80, q_load=0.60),
            ],
            branches=[
                Branch(1, 2, r=0.02, x=0.20, b=0.02),
                Branch(1, 3, r=0.01, x=0.15, b=0.01),
                Branch(2, 3, r=0.03, x=0.25, b=0.02),
            ],
        )

        result = solve_power_flow(
            net,
            tolerance=1e-8,
            max_iterations=30,
            enforce_q_limits=True,
        )

        self.assertTrue(result["converged"])

        switches = result["q_limit_switches"]
        self.assertTrue(
            any(
                s["bus"] == 2
                and s["from"] == "PV"
                and s["to"] == "PQ"
                for s in switches
            )
        )

        # No bus should switch in both directions in the same iteration.
        for iteration in set(
            entry["iteration"] for entry in result["iteration_log"]
        ):
            transitions = [
                s for s in switches
                if s.get("iteration") == iteration and s["bus"] == 2
            ]

            self.assertFalse(
                any(
                    s["from"] == "PV" and s["to"] == "PQ"
                    for s in transitions
                )
                and any(
                    s["from"] == "PQ" and s["to"] == "PV"
                    for s in transitions
                )
            )

    def test_pq_bus_switches_back_to_pv(self):
        """Q limits are tested at converged points, then buses are re-switched and re-converged.
        This 5-bus case (found by random search) needs PV->PQ at buses 3 and 4 and then
        PQ->PV back-switching of bus 3 once the voltage profile has changed."""
        net = Network(
            buses=[
                Bus(1, "SLACK", vm=1.02),
                Bus(2, "PV", vm=1.004, p_gen=0.07, q_min=-0.24, q_max=0.17),
                Bus(3, "PV", vm=1.005, p_gen=0.13, q_min=-0.14, q_max=0.05),
                Bus(4, "PV", vm=1.037, p_gen=0.03, q_min=-0.12, q_max=0.16),
                Bus(5, "PQ", p_load=0.34, q_load=0.23),
            ],
            branches=[
                Branch(1, 2, 0.01, 0.1), Branch(2, 3, 0.02, 0.1), Branch(3, 4, 0.02, 0.15),
                Branch(4, 5, 0.02, 0.2), Branch(1, 5, 0.03, 0.2), Branch(2, 5, 0.02, 0.12),
            ],
        )
        original_q = [b.q_gen for b in net.buses]
        result = solve_power_flow(net, tolerance=1e-8, max_iterations=30, enforce_q_limits=True)
        self.assertTrue(result["converged"])
        self.assertLessEqual(result["max_mismatch_pu"], 1e-8)
        sw = [(s["bus"], s["from"], s["to"], s["limit"]) for s in result["q_limit_switches"]]
        self.assertIn((3, "PV", "PQ", "Qmin"), sw)
        self.assertIn((4, "PV", "PQ", "Qmax"), sw)
        self.assertIn((3, "PQ", "PV", "Qmin"), sw)
        self.assertLess(sw.index((3, "PV", "PQ", "Qmin")), sw.index((3, "PQ", "PV", "Qmin")))
        by = {b["bus"]: b for b in result["bus_results"]}
        # final state: bus 3 is a regulating PV bus again, bus 4 is held at its Qmax
        self.assertEqual(by[3]["type"], "PV")
        self.assertAlmostEqual(by[3]["vm_pu"], 1.005, places=9)
        self.assertTrue(-0.14 - 1e-6 <= by[3]["q_injection_pu"] <= 0.05 + 1e-6)
        self.assertTrue(by[4]["q_limit_hit"])
        self.assertAlmostEqual(by[4]["q_injection_pu"], 0.16, places=6)
        self.assertLessEqual(by[4]["vm_pu"], 1.037)          # held at Qmax => voltage below set-point
        # the solve must not modify the input model
        self.assertEqual([b.q_gen for b in net.buses], original_q)


if __name__ == "__main__":
    unittest.main()
