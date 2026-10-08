"""New content changes active picture while preserving the CVBS timing source."""
import unittest
import numpy as np
import waveforms as V


class ContentTests(unittest.TestCase):
    def test_patterns_preserve_sync_burst_and_calibration_patches(self):
        for standard in ('PAL','NTSC'):
            base,region=V.raster(standard,short=True,stimulus_seed=71601)
            fixed=np.isin(region,[0,2,3,4,5])
            for pattern in ('zoneplate','checker','texture'):
                image,rr=V.raster(standard,short=True,stimulus_seed=71601,pattern=pattern)
                self.assertTrue(np.array_equal(region,rr))
                self.assertTrue(np.array_equal(base[fixed],image[fixed]))
                self.assertGreater(np.std((image-base)[region==1]),10)
                self.assertTrue(np.all(np.isfinite(image)))
                self.assertTrue(np.array_equal(image,V.raster(standard,short=True,stimulus_seed=71601,pattern=pattern)[0]))

    def test_unknown_pattern_refused(self):
        with self.assertRaises(ValueError):V.raster('PAL',pattern='unknown')


if __name__=='__main__':unittest.main()
