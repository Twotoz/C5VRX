"""Host-only predictive PLL checks for C5VRX."""
from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research'))
import predictive_oracle as P


class PredictiveOracleTest(unittest.TestCase):
    def test_steady_frequency_and_duplicate_output(self):
        for hz in [-4e6,0.,6e6]:
            t=np.arange(8192)/40e6
            raw=P.E.D.sim.quantize_iq(4*np.exp(2j*np.pi*hz*t),'ultrafine')
            expected=np.rint((hz+6e6)/15e6*63)
            for frequency in [False,True]:
                out=P.codes(raw,.7,.05,frequency)
                self.assertLessEqual(abs(float(np.mean(out[2000:]))-expected),1.)
                np.testing.assert_array_equal(out[::2],out[1::2])


if __name__=='__main__':unittest.main()
