
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
        net = Network(
            buses=[
                Bus(1, "SLACK", vm=1.0),
                Bus(
                    2, "PV",
                    vm=1.02,
                    p_gen=0.0,
                    q_min=-0.10,
                    q_max=0.30,
                ),
            ],
            branches=[
                Branch(1, 2, r=0.01, x=0.20),
            ],
        )

        original_q_gen = net.buses[1].q_gen

        result = solve_power_flow(
            net,
            tolerance=1e-8,
            max_iterations=30,
            enforce_q_limits=True,
            initial_va_deg=[0.0, 60.0],
        )

        self.assertTrue(result["converged"])
        self.assertLessEqual(result["max_mismatch_pu"], 1e-8)

        switches = [
            s for s in result["q_limit_switches"]
            if s["bus"] == 2
        ]

        transitions = [
            (s["from"], s["to"])
            for s in switches
        ]

        self.assertIn(("PV", "PQ"), transitions)
        self.assertIn(("PQ", "PV"), transitions)

        pv_to_pq = next(
            s for s in switches
            if s["from"] == "PV" and s["to"] == "PQ"
        )

        pq_to_pv = next(
            s for s in switches
            if s["from"] == "PQ" and s["to"] == "PV"
        )

        self.assertGreater(
            pq_to_pv["iteration"],
            pv_to_pq["iteration"],
        )

        bus2 = next(
            b for b in result["bus_results"]
            if b["bus"] == 2
        )

        self.assertEqual(bus2["type"], "PV")
        self.assertFalse(bus2["q_limit_hit"])
        self.assertAlmostEqual(bus2["vm_pu"], 1.02, places=6)

        self.assertGreaterEqual(
            bus2["q_injection_pu"], -0.10 - 1e-8
        )
        self.assertLessEqual(
            bus2["q_injection_pu"], 0.30 + 1e-8
        )

        # Solving must not permanently modify the input generator setting.
        self.assertEqual(net.buses[1].q_gen, original_q_gen)


if __name__ == "__main__":
    unittest.main()
