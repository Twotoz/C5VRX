"""Independent mux-compatible source, causality and signed-arithmetic proofs."""
import unittest
import numpy as np
import counter_diff as D
import counter_bayes as B
import counter_history as H
import counter_quality as Q
import bs_model as BS

class CounterTest(unittest.TestCase):
 def test_every_schedule_matches_full_bytes_and_stream_cadence(self):
  raw=np.random.default_rng(1920190).integers(0,256,70000,dtype=np.uint8)
  models=[(D.synthesize(False),D.source,lambda r,m:D.codes(r,np.asarray(m['lut'],np.uint16),False)),
          (D.synthesize(True),D.source,lambda r,m:D.codes(r,np.asarray(m['lut'],np.uint16),True)),
          (B.build(),B.source,lambda r,m:B.trace(r,np.asarray(m['lut'],np.uint16))[0]),
          (H.build(),H.source,lambda r,m:H.trace(r,np.asarray(m['lut'],np.uint16))[0]),
          (Q.build(np.arange(256)%32),Q.source,lambda r,m:Q.trace(r,np.asarray(m['lut'],np.uint16))[0])]
  for m,source,fn in models:
   s=source(m);cfg,lut,blocks,_=BS.parse(s);self.assertEqual(len(blocks),8);self.assertEqual(len(lut),1024)
   self.assertEqual(cfg['lut_width_bits'],'16');stats={}
   actual=BS.simulate(s,raw,len(raw)//2,wrap_rom=True,stats=stats)
   np.testing.assert_array_equal(np.asarray(actual),fn(raw,m))
   self.assertEqual(stats['bundles'],len(raw)-1)
   np.testing.assert_array_equal(fn(raw[:512],m),fn(raw,m)[:256])
 def test_history_recovers_frequency_and_two_past_bits_despite_counter_carry(self):
  for dp in range(-16,16):
   for h1 in (0,1):
    for h0 in (0,1):
     for carry in (0,1):
      d=(8*dp+80+1+2*h1+4*h0+carry)&255
      f,h=H.observation(d)
      self.assertEqual(h,h1+2*h0)
      self.assertAlmostEqual(f,dp*20e6/32)
 def test_absolute_frequency_polarity_and_scale_on_independent_cw(self):
  from iq_lanes import quantize
  import hardware as HW
  t=np.arange(4096)/40e6
  m=D.synthesize(False)
  for f in (-2e6,1e6,3e6):
   raw=quantize(7*np.exp(2j*np.pi*f*t),'ultrafine')
   out=D.codes(raw,np.asarray(m['lut'],np.uint16),False)[32:]
   expected=np.clip((f*256/20e6-HW.B.P.OFFSET)*HW.B.P.SCALE,0,63)
   self.assertLess(abs(float(out.mean())-expected),.6)
   self.assertLess(float(np.percentile(abs(out-expected),99)),4.)
 def test_calibration_never_changes_phase_or_history_words(self):
  m=H.build();original=np.asarray(m['lut'],np.uint16)
  for dev,c in ((.69,.55484e6),(1.4,1.5744e6)):
   mm=H.remap(m,dev,c);changed=np.asarray(mm['lut'],np.uint16)
   np.testing.assert_array_equal(changed[256:],original[256:])
   self.assertTrue((changed[:256]<64).all())

if __name__=='__main__':unittest.main()
