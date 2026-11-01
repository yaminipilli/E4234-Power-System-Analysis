
import unittest
from pathlib import Path

from matpower_import import load_matpower_case
from solver import solve_power_flow


class TestIEEE14(unittest.TestCase):

    def test_ieee14_converges(self):
        case_path = (
            Path(__file__).resolve().parents[1]
            / "data"
            / "case14.m"
        )

        net = load_matpower_case(case_path)

        self.assertEqual(len(net.buses), 14)
        self.assertEqual(len(net.branches), 20)

        result = solve_power_flow(
            net,
            tolerance=1e-8,
            max_iterations=30,
            enforce_q_limits=False,
        )

        self.assertTrue(result["converged"])
        self.assertLessEqual(
            result["max_mismatch_pu"], 1e-8
        )
        self.assertAlmostEqual(
            result["total_p_loss_mw"],
            13.3933,
            delta=0.02,
        )

        # Check the reference and generator voltage setpoints.
        expected_vm = {
            1: 1.060,
            2: 1.045,
            3: 1.010,
            6: 1.070,
            8: 1.090,
        }

        bus_results = {
            b["bus"]: b for b in result["bus_results"]
        }

        for bus_number, expected in expected_vm.items():
            self.assertAlmostEqual(
                bus_results[bus_number]["vm_pu"],
                expected,
                places=6,
            )


if __name__ == "__main__":
    unittest.main()
