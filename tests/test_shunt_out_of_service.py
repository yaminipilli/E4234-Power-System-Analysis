
import unittest
import numpy as np

from network import Bus, Branch, Network


class TestShuntsAndOutOfService(unittest.TestCase):

    def test_bus_shunt_is_added_to_ybus(self):
        net = Network(
            buses=[
                Bus(1, "SLACK", g_shunt=0.01, b_shunt=0.05),
                Bus(2, "PQ"),
            ],
            branches=[
                Branch(1, 2, r=0.02, x=0.10, b=0.04)
            ],
        )

        Y = net.ybus().toarray()
        y = 1 / complex(0.02, 0.10)

        expected = y + 1j * 0.04 / 2 + complex(0.01, 0.05)

        np.testing.assert_allclose(Y[0, 0], expected, atol=1e-12)

    def test_out_of_service_branch_is_excluded(self):
        net = Network(
            buses=[
                Bus(1, "SLACK"),
                Bus(2, "PQ"),
            ],
            branches=[
                Branch(
                    1, 2,
                    r=0.02,
                    x=0.10,
                    b=0.04,
                    in_service=False,
                )
            ],
        )

        Y = net.ybus().toarray()

        np.testing.assert_allclose(
            Y, np.zeros((2, 2), dtype=complex), atol=1e-12
        )


if __name__ == "__main__":
    unittest.main()
