"""Capture-bit folding must preserve sign and match coarse signed Q4."""
import unittest
import numpy as np
from iq_lanes import quantize
import hardware as H
from video_metrics import burst


class LaneTests(unittest.TestCase):
    def test_coarse_matches_existing_quantizer(self):
        rng=np.random.default_rng(24617);z=rng.uniform(-25,25,10000)+1j*rng.uniform(-25,25,10000)
        self.assertTrue(np.array_equal(quantize(z,'coarse'),H.B.D.raw_bytes(z)))

    def test_fine_folding_and_adc_rails(self):
        x=np.array([0,.99,1,7.99,8,8.99,15.99,16,-.01,-8,-8.01,-16,-16.01])
        i,_=H.B.D.cells(quantize(x.astype(complex),'fine'))
        self.assertEqual(i.tolist(),[0,0,1,7,0,0,7,7,-1,-8,-1,-8,-8])

    def test_sign_survives_every_adc_code(self):
        adc=np.arange(-512,512)
        for lane,shift in [('coarse',6),('fine',5),('ultrafine',4)]:
            i,_=H.B.D.cells(quantize(adc.astype(complex)/(1<<shift),lane))
            self.assertTrue(np.array_equal(i<0,adc<0))

    def test_burst_projection_reports_gain_and_phase(self):
        t=np.arange(12000)/40e6;phase=2*np.pi*4433618.75*t
        region=np.zeros(len(t),np.uint8)
        for start in (4000,6500,9000):region[start:start+90]=3
        truth=np.where(region==3,20*np.cos(phase),0)
        y=np.where(region==3,16*np.cos(phase+np.pi/18),0)
        r=burst(y,dict(truth=truth,region=region,calibration=(0,1,0),standard='PAL'))
        self.assertAlmostEqual(r['burst_gain'],.8,places=10)
        self.assertAlmostEqual(r['burst_phase_deg'],10,places=10)
        self.assertLess(r['burst_phase_jitter_deg'],1e-8)


if __name__=='__main__':unittest.main()
