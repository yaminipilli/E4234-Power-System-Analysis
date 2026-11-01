import unittest
import numpy as np

from network import Bus, Branch, Network
from solver import calc_power, _jacobian


class TestJacobian(unittest.TestCase):

    def test_analytical_jacobian_matches_finite_difference(self):
        # Three-bus network: one slack, one PV and one PQ bus.
        net = Network(
            buses=[
                Bus(1, "SLACK", vm=1.04, va_deg=0.0),
                Bus(2, "PV", vm=1.02, va_deg=-3.0,
                    p_gen=0.50),
                Bus(3, "PQ", vm=0.98, va_deg=-5.0,
                    p_load=0.60, q_load=0.25),
            ],
            branches=[
                Branch(1, 2, r=0.02, x=0.20, b=0.02),
                Branch(1, 3, r=0.01, x=0.15, b=0.01),
                Branch(2, 3, r=0.03, x=0.25, b=0.02),
            ],
        )

        Ybus = net.ybus()
        vm = np.array([1.04, 1.02, 0.98])
        va = np.deg2rad([0.0, -3.0, -5.0])

        # Slack angle is fixed; only PQ voltage magnitude is unknown.
        angle_buses = np.array([1, 2])
        pq_buses = np.array([2])

        p_calc, q_calc = calc_power(Ybus, vm, va)

        # Analytical Jacobian.
        J_analytical = _jacobian(
            Ybus, vm, va, p_calc, q_calc,
            angle_buses, pq_buses
        ).toarray()

        # State vector: [theta_2, theta_3, V_3].
        x = np.r_[va[angle_buses], vm[pq_buses]]

        def calculated_mismatch_state(x_trial):
            va_trial = va.copy()
            vm_trial = vm.copy()

            va_trial[angle_buses] = x_trial[:len(angle_buses)]
            vm_trial[pq_buses] = x_trial[len(angle_buses):]

            p, q = calc_power(Ybus, vm_trial, va_trial)

            return np.r_[p[angle_buses], q[pq_buses]]

        # Central finite differences independently estimate each column.
        h = 1e-6
        J_numerical = np.zeros_like(J_analytical)

        for k in range(len(x)):
            x_plus = x.copy()
            x_minus = x.copy()

            x_plus[k] += h
            x_minus[k] -= h

            f_plus = calculated_mismatch_state(x_plus)
            f_minus = calculated_mismatch_state(x_minus)

            J_numerical[:, k] = (f_plus - f_minus) / (2 * h)

        max_error = np.max(
            np.abs(J_analytical - J_numerical)
        )

        print(f"\nMaximum Jacobian error: {max_error:.3e}")

        np.testing.assert_allclose(
            J_analytical,
            J_numerical,
            rtol=1e-5,
            atol=1e-7,
            err_msg="Analytical Jacobian does not match finite differences",
        )


if __name__ == "__main__":
    unittest.main()