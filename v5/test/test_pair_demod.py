"""Exhaustive decoder invariants; C5VRX by Twotoz and the contributors."""
from pathlib import Path
import re
import sys
import unittest
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'research'))
import gen_pair_demod as G
import sim


class PairDemodTest(unittest.TestCase):
    def test_original_generator_is_bit_exact(self):
        for kp, name in [(.85, 'u85q'), (.6, 'u85q_kp060'), (.5, 'u85q_kp050')]:
            np.testing.assert_array_equal(G.build(kp=kp), sim.u85q_lut(ROOT / 'main' / (name + '.bsasm')))

    def test_high_radius_decoder_is_identical_for_every_second_sample(self):
        za, _ = G.vectors('4411')
        high = abs(za) >= 2.5
        for kp, qg, name in [(.85, .5, 'u85g')]:
            new = G.build(kp=kp, weight=1., rotation=.4, assist_radius=2.5, quad_gain=qg)
            old = G.build(kp=kp)
            np.testing.assert_array_equal(new[high] >> 11, old[high] >> 11)
            np.testing.assert_array_equal(new, sim.u85q_lut(ROOT / 'main' / (name + '.bsasm')))
            self.assertTrue(np.any((new >> 11) != (old >> 11)))
            # All 32x32 transitions use the same state and DAC encoding;
            # wide mode's lower eleven bits are exactly the original ones.
            if kp == .85:
                np.testing.assert_array_equal(new & 2047, old & 2047)
            else:
                address = np.arange(1024)
                non_origin_token = (address & 31) < 28
                np.testing.assert_array_equal((new & 2047)[non_origin_token], (old & 2047)[non_origin_token])

    def test_ultrafine_quantizer_folds_instead_of_clipping(self):
        # Exhaust all 1024 physical signed ADC codes against the RF tap bits.
        adc = np.arange(-512, 512)
        y = adc / 16 + .01 + 1j * (adc / 16 + .01)
        packed = sim.quantize_iq(y, 'ultrafine')
        nibble = ((adc >> 4) & 7) | (((adc >> 9) & 1) << 3)
        np.testing.assert_array_equal(packed, (nibble << 4) | nibble)
        self.assertEqual(int(sim.quantize_iq(np.array([8. + 0j]), 'ultrafine')[0]) >> 4, 0)
        self.assertEqual(int(sim.quantize_iq(np.array([-9. + 0j]), 'ultrafine')[0]) >> 4, 15)
        low = (adc >= -128) & (adc < 128)
        np.testing.assert_array_equal(packed[low], sim.quantize_iq(y[low], 'clip'))

    def test_full_raw_pair_address_and_schedule(self):
        bits = G.pair_bits('4411')
        words = np.arange(65536, dtype=np.uint32)
        address = sum(((words >> bit) & 1) << j for j, bit in enumerate(bits))
        expected = ((words >> 4) & 15) | ((words & 15) << 4) | (((words >> 15) & 1) << 8) | (((words >> 11) & 1) << 9)
        np.testing.assert_array_equal(address, expected)
        # LUT-only change: every executable instruction is the tested U85Q
        # schedule. No new stage, DMA reset, register source or read cadence.
        old = (ROOT / 'main' / 'u85q.bsasm').read_text()
        new = (ROOT / 'main' / 'u85g.bsasm').read_text()
        def instructions(text):
            return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith(('#', 'lut '))]
        self.assertEqual(instructions(old), instructions(new))
        self.assertEqual(new.count('read 16,'), 4)
        self.assertEqual(new.count('write 16,'), 4)
        self.assertEqual(len(re.findall(r'^(?:controller|worker)_\d+:', new, re.M)), 8)


if __name__ == '__main__':
    unittest.main()
