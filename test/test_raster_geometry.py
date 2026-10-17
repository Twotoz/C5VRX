"""C5VRX by Twotoz/contributors: source sync geometry, not keeper output.

Independent BT.470 half-line oracle checks both field parities, every pre/
broad/post pulse and blank slot. Historical input hashes remain explicit.
"""
import hashlib
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'research'))
import sim


def source_slot(standard, h):
    # B/G PAL field reference is the first broad pulse; system M reference
    # is first pre-EQ. Derived from BT.470 Fig2-1/2-2 and Table1-2, rather
    # than inferring V from the source's own ACTIVE labels or keeper output.
    field_halves, count, origin = (625, 5, 5) if standard == 'PAL' else (525, 6, 0)
    pos = (h + origin) % field_halves
    return 1 if pos < count or 2 * count <= pos < 3 * count else 2 if pos < 2 * count else 0


class RasterGeometry(unittest.TestCase):
    def test_both_parities_have_complete_pulses_and_blank_slots(self):
        for standard in ('PAL', 'NTSC'):
            half_count = 625 if standard == 'PAL' else 525
            exact_half = 1280. if standard == 'PAL' else 11440. / 9.
            for seed in (220, 772):
                with self.subTest(standard=standard, seed=seed):
                    ire, region = sim.raster(standard, seed, 4)
                    for h in range(half_count * 4):
                        a, b = sim.half_sample(standard, h), sim.half_sample(standard, h + 1)
                        self.assertLessEqual(abs(a - h * exact_half), 2.000001)
                        kind = source_slot(standard, h)
                        if not kind:
                            continue
                        width = b - a - 188 if kind == 2 else 94 if standard == 'PAL' else 92
                        self.assertTrue(np.all(ire[a:a + width] == -40.), (standard, h))
                        self.assertTrue(np.all(region[a:a + width] == 2), (standard, h))
                        self.assertTrue(np.all(ire[a + width:b] == 0.), (standard, h))
                        self.assertTrue(np.all(region[a + width:b] == 0), (standard, h))

    def test_only_skipped_slots_change_picture_rng_is_preserved(self):
        expected = {'PAL': [1245, 2495], 'NTSC': [525, 1575]}
        for standard in ('PAL', 'NTSC'):
            old, old_region = sim.raster_legacy(standard, 220, 4)
            fixed, region = sim.raster(standard, 220, 4)
            differences = (old != fixed) | (old_region != region)
            mask = np.zeros(len(old), bool)
            conflicting = []
            halves = 2500 if standard == 'PAL' else 2100
            for h in range(halves):
                a, b = sim.half_sample(standard, h), sim.half_sample(standard, h + 1)
                if np.any(differences[a:b]):
                    conflicting.append(h)
                if h in expected[standard]:
                    mask[a:b] = True
            self.assertEqual(conflicting, expected[standard])
            self.assertFalse(np.any(differences & ~mask))
            # The first field is unchanged; no broad evidence regeneration.
            first, first_region = sim.raster(standard, 220, 1)
            legacy, legacy_region = sim.raster_legacy(standard, 220, 1)
            np.testing.assert_array_equal(first, legacy)
            np.testing.assert_array_equal(first_region, legacy_region)

    def test_retained_pal_counterexample_and_corrected_source_hashes(self):
        wanted = [('PAL', True, '7e4a002f241e8c64fd274576a14c6026a68e4c75f40ce06857f0a297b9b52daf'),
                  ('PAL', False, 'cd084cf442f0a36aaaa9c71f7715c83807846f69f648ca0df7bae60b9ff8912f'),
                  ('NTSC', True, '395f696734d0b85804e50b71cbb2a042d7d7f87ba141503260c1e1f7864e330a')]
        for standard, legacy, digest in wanted:
            case = sim.make_case(213, 30., standard, 1., filter_hz=12e6, fields=3,
                                 physical_adc=True, legacy_raster=legacy)
            raw = case['raw'].copy(); lo, hi = len(raw) * 19 // 30, len(raw) * 22 // 30
            raw[lo:hi] = np.random.default_rng(212).integers(0, 256, hi - lo, dtype=np.uint8)
            self.assertEqual(hashlib.sha256(raw.tobytes()).hexdigest(), digest)


if __name__ == '__main__':
    unittest.main()
