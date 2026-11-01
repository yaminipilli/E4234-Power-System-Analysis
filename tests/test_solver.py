import unittest
from network import Bus, Branch, Network
from solver import solve_power_flow

class TestYbus(unittest.TestCase):
    def test_two_bus_ybus(self):
        net=Network([Bus(1,'SLACK'),Bus(2,'PQ')],[Branch(1,2,0.01,0.1,b=0.02)])
        Y=net.ybus().toarray(); y=1/complex(.01,.1)
        self.assertAlmostEqual(Y[0,1].real,(-y).real,places=10)
        self.assertAlmostEqual(Y[0,1].imag,(-y).imag,places=10)
        self.assertAlmostEqual(Y[0,0].imag,(y+1j*.01).imag,places=10)

class TestPowerFlow(unittest.TestCase):
    def test_two_bus_converges(self):
        net=Network([Bus(1,'SLACK',vm=1.04),Bus(2,'PQ',p_load=1.0,q_load=.3)],
                    [Branch(1,2,r=.02,x=.2,b=.04)])
        res=solve_power_flow(net,tolerance=1e-10,max_iterations=30,enforce_q_limits=False)
        self.assertTrue(res['converged']); self.assertLessEqual(res['max_mismatch_pu'],1e-10)
        self.assertLess(res['bus_results'][1]['vm_pu'],1.04); self.assertGreater(res['total_p_loss_mw'],0)

if __name__=='__main__': unittest.main()
