"""Host-only full-Q4 oracle checks; C5VRX by Twotoz/contributors."""
from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research'))
import floating_oracle as F


class FloatingOracleTest(unittest.TestCase):
    def test_constant_phase_has_zero_steady_frequency(self):
        raw=F.E.D.sim.quantize_iq(np.full(4096,4+0j),'ultrafine')
        out=F.codes(raw,.85,.5)
        self.assertTrue(np.all(out[1000:]==25))

    def test_adjacent_pair_motion_is_not_wrapped_again(self):
        t=np.arange(4096)/40e6
        raw=F.E.D.sim.quantize_iq(4*np.exp(2j*np.pi*12e6*t),'ultrafine')
        out=F.codes(raw,1.,1.)
        # Adjacent motion is below pi; its sum exceeds pi. Rewrapping would
        # map +12 MHz to -8 MHz and emit the bottom rail instead of the top.
        self.assertEqual(float(np.median(out[1000:])),63.)
        np.testing.assert_array_equal(out[::2],out[1::2])


if __name__=='__main__':unittest.main()
