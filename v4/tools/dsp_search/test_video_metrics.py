"""Missing sync must count as timing failure; recovering a train is improvement."""
import unittest
import numpy as np
from video_metrics import waveform,relative_quality,dropout
from leaderboard import frontier
import waveforms as V


class MetricsTests(unittest.TestCase):
    def test_independent_video_patterns_preserve_sync_and_flat_levels(self):
        a,region=V.raster('PAL',short=True,stimulus_seed=1)
        b,other=V.raster('PAL',short=True,stimulus_seed=2)
        repeat,_=V.raster('PAL',short=True,stimulus_seed=1)
        self.assertTrue(np.array_equal(a,repeat))
        self.assertTrue(np.array_equal(region,other))
        self.assertTrue(np.array_equal(a[region==2],b[region==2]))
        self.assertTrue(np.all(a[region==4]==100))
        self.assertTrue(np.all(a[region==5]==0))
        self.assertGreater(np.mean(abs(a[region==1]-b[region==1])),1)

    def test_missing_pulses_cannot_improve_jitter(self):
        truth=np.zeros(40000)
        for start in range(4000,36000,2560):truth[start:start+188]=-40
        case=dict(truth=truth,region=np.zeros(len(truth)),calibration=(0,1,0))
        broken=truth.copy();broken[14240:14428]=0
        good=waveform(truth,case);bad=waveform(broken,case)
        self.assertEqual(good['h_missing'],0)
        self.assertEqual(bad['h_missing'],1)
        self.assertEqual(bad['max_h_gap'],1)
        self.assertEqual(bad['h_jitter_matched_us'],0)
        self.assertGreater(bad['h_jitter_us'],0)

    def test_restoring_vertical_train_passes_relative_guard(self):
        truth=np.zeros(40000)
        truth[5000:6000]=-40;truth[25000:26000]=-40
        case=dict(truth=truth,region=np.zeros(len(truth)),calibration=(0,1,0))
        broken=truth.copy();broken[25000:26000]=0
        good=waveform(truth,case);bad=waveform(broken,case)
        self.assertEqual(good['expected_v_trains'],2)
        self.assertEqual(bad['v_trains'],1)
        self.assertNotIn('vertical_trains',relative_quality(good,bad))
        self.assertIn('vertical_trains',relative_quality(bad,good))

    def test_false_pulses_reported_even_when_no_true_sync_is_missing(self):
        truth=np.zeros(16000)
        truth[5000:5188]=-40;truth[10000:10188]=-40
        case=dict(truth=truth,region=np.zeros(len(truth)),calibration=(0,1,0))
        noisy=truth.copy();noisy[7500:7580]=-40
        good=waveform(truth,case);bad=waveform(noisy,case)
        self.assertEqual(good['false_sync_pulses'],0)
        self.assertEqual(bad['h_missing'],0)
        self.assertEqual(bad['false_sync_pulses'],1)
        self.assertEqual(bad['false_sync_per_line'],.5)
        # Without any expected sync, every detected excursion is spurious.
        empty=dict(case,truth=np.zeros(len(truth)))
        self.assertEqual(dropout(noisy,empty)['false_sync_pulses'],3)

    def test_frontier_preserves_independent_tradeoffs(self):
        rows=[dict(id=name,weak_sinad=w,weak_missing=m,strong_sinad=s)
              for name,w,m,s in [('a',10,2,15),('b',9,1,15),('c',9,3,14),('d',9,2,16)]]
        self.assertEqual({r['id'] for r in frontier(rows)},{'a','b','d'})


if __name__=='__main__':unittest.main()
