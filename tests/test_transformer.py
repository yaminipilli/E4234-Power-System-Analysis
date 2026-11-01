
import unittest
import numpy as np

from network import Bus, Branch, Network


class TestTransformerYbus(unittest.TestCase):

    def test_off_nominal_tap_ratio(self):
        """Check Ybus entries for a transformer with a non-unity tap."""

        net = Network(
            buses=[
                Bus(1, "SLACK"),
                Bus(2, "PQ"),
            ],
            branches=[
                Branch(
                    from_bus=1,
                    to_bus=2,
                    r=0.02,
                    x=0.10,
                    b=0.04,
                    tap=1.10,
                    shift_deg=0.0,
                )
            ],
        )

        Y = net.ybus().toarray()

        y = 1 / complex(0.02, 0.10)
        tap = 1.10

        yff = (y + 1j * 0.04 / 2) / tap**2
        yft = -y / tap
        ytf = -y / tap
        ytt = y + 1j * 0.04 / 2

        np.testing.assert_allclose(Y[0, 0], yff, atol=1e-12)
        np.testing.assert_allclose(Y[0, 1], yft, atol=1e-12)
        np.testing.assert_allclose(Y[1, 0], ytf, atol=1e-12)
        np.testing.assert_allclose(Y[1, 1], ytt, atol=1e-12)

    def test_phase_shift_makes_ybus_asymmetric(self):
        """Check phase-shifting transformer off-diagonal entries."""

        net = Network(
            buses=[
                Bus(1, "SLACK"),
                Bus(2, "PQ"),
            ],
            branches=[
                Branch(
                    from_bus=1,
                    to_bus=2,
                    r=0.02,
                    x=0.10,
                    b=0.04,
                    tap=1.05,
                    shift_deg=10.0,
                )
            ],
        )

        Y = net.ybus().toarray()

        y = 1 / complex(0.02, 0.10)
        t = 1.05 * np.exp(1j * np.deg2rad(10.0))

        expected_yft = -y / np.conj(t)
        expected_ytf = -y / t

        np.testing.assert_allclose(
            Y[0, 1], expected_yft, atol=1e-12
        )
        np.testing.assert_allclose(
            Y[1, 0], expected_ytf, atol=1e-12
        )

        # A nonzero phase shift should make these entries unequal.
        self.assertGreater(abs(Y[0, 1] - Y[1, 0]), 1e-6)


if __name__ == "__main__":
    unittest.main()
