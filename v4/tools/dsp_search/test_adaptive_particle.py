"""Likelihood, causality, gain inference and ambiguity checks for new teacher."""
import unittest
import numpy as np
import adaptive_particle as B
from omega_bayes import component_likelihood
from iq_lanes import quantize

class ParticleTest(unittest.TestCase):
    def test_folded_likelihood_is_probability_and_matches_sampled_quantizer(self):
        rng=np.random.default_rng(231091);mu=np.array([-10.,-3.2,.5,7.1,10.])
        sigma=.6;mass=component_likelihood(mu,sigma)
        np.testing.assert_allclose(mass.sum(1),1.,atol=2e-14)
        for m,p in zip(mu,mass):
            raw=quantize(m+sigma*rng.normal(size=100000)+0j,'ultrafine')>>4
            hist=np.bincount(raw,minlength=16)/len(raw)
            self.assertLess(abs(hist-p).max(),.007)
    def test_future_iq_cannot_change_past_output(self):
        c=B.setup(particles=512);raw=np.random.default_rng(771).integers(0,256,4096,dtype=np.uint8)
        h,x=B.filter(raw,c);hh,xx=B.filter(raw[:2048],c)
        np.testing.assert_array_equal(h[:1024],hh)
        np.testing.assert_array_equal(x[:1024],xx)
    def test_gain_is_inferred_and_cw_frequency_has_correct_polarity(self):
        c=B.setup(particles=1024)
        t=np.arange(8192)/40e6
        for amp,freq in ((3.,-2e6),(7.,1e6)):
            raw=quantize(amp*np.exp(2j*np.pi*freq*t),'ultrafine')
            h,x=B.filter(raw,c)
            self.assertLess(abs(h[1024:].mean()-freq),150000.)
            self.assertLess(abs(x[1024:,2].mean()-amp),1.)
    def test_unknown_gain_step_recovers_even_folded_cw(self):
        c=B.setup(particles=2048,modes=True)
        t=np.arange(16384)/40e6;amp=np.where(np.arange(len(t))<8192,3.,10.)
        h,x=B.filter(quantize(amp*np.exp(2j*np.pi*1e6*t),'ultrafine'),c)
        for lo,hi,a in ((1024,4096,3.),(5120,8192,10.)):
            self.assertLess(abs(h[lo:hi].mean()-1e6),150000.)
            self.assertLess(abs(x[lo:hi,2].mean()-a),.5)
    def test_zero_signal_does_not_leak_truth_and_reports_uncertainty(self):
        c=B.setup(particles=512)
        r=quantize(np.random.default_rng(552).normal(size=4096)+1j*np.random.default_rng(553).normal(size=4096),'ultrafine')
        h,x=B.filter(r,c)
        self.assertTrue(np.isfinite(h).all());self.assertTrue(np.isfinite(x).all())
        self.assertGreater(float(x[512:,5].mean()),.1)
if __name__=='__main__':unittest.main()
