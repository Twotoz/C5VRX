"""Storage, causal simulation and promotion gates for C5VRX's offline search."""
from dataclasses import replace
from pathlib import Path
import re
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'research'))
import demod_search as D


def reference_codes(raw, lut, d):
    """Independent integer interpreter of the two lookup field wiring.

    Does not call the simulator's pair_bits/address/token/state helpers.
    """
    out = np.zeros(len(raw), np.uint8)
    state = 0
    states = 1 << (10 - d.token_bits)
    for k in range(len(raw) // 2 - 1):
        word = int(raw[2 * k]) | int(raw[2 * k + 1]) << 8
        address, shift = 0, 0
        for keep, base in zip(map(int, d.layout), [4, 0, 12, 8]):
            address |= ((word >> (base + 4 - keep)) & ((1 << keep) - 1)) << shift
            shift += keep
        token = int(lut[address]) >> (16 - d.token_bits)
        value = int(lut[state * (1 << d.token_bits) + token])
        state = (value >> 6) % states
        out[2 * k + 2:2 * k + 4] = value & 63
    return out


class DemodSearchTest(unittest.TestCase):
    def test_control_is_original_bit_exact(self):
        np.testing.assert_array_equal(D.build(D.Design()), D.sim.u85q_lut())

    def test_all_population_storage_and_integer_execution(self):
        raw = np.random.default_rng(761).integers(0, 256, 512, dtype=np.uint8)
        designs = D.population(210011, 96)
        self.assertEqual(len({D.design_id(d) for d in designs}), 96)
        self.assertEqual(designs, D.population(210011, 96))
        self.assertEqual({d.family for d in designs}, {'tracker', 'predictive'})
        self.assertEqual({d.token_bits for d in designs}, {4, 5, 6})
        for d in designs:
            lut = D.build(d)
            state_bits, _ = D.geometry(d)
            self.assertEqual(lut.nbytes, 2048)
            self.assertTrue(np.all(lut >> (16 - d.token_bits) < 1 << d.token_bits))
            state = (lut >> 6) & ((1 << state_bits) - 1)
            self.assertTrue(np.all((state << d.token_bits) < 1024))
            expected = reference_codes(raw, lut, d)
            actual = D.sim._pair_codes(raw, lut, D.G.pair_bits(d.layout), d.token_bits)
            np.testing.assert_array_equal(expected, actual)
            self.assertTrue(np.all(actual[::2] == actual[1::2]))

    def test_program_state_token_ranges_and_schedule(self):
        for d in D.population(761, 32):
            text = D.program(d)
            state_bits, _ = D.geometry(d)
            self.assertEqual(len(text.split('lut ')[1].splitlines()[0].split()), 1024)
            self.assertEqual(text.count('read 16,'), 4)
            self.assertEqual(text.count('write 16,'), 4)
            self.assertEqual(text.count('    nop'), 8)
            self.assertEqual(len(re.findall(r'^(?:controller|worker)_\d+:', text, re.M)), 8)
            self.assertIn(f'set 26..{25 + state_bits} L6..L{5 + state_bits}', text)
            self.assertIn(f'set {16 + d.token_bits}..25 O26..O{25 + state_bits}', text)
            self.assertNotIn('O32', text)

    def test_reject_storage_that_needs_seventh_retained_bit(self):
        with self.assertRaises(ValueError):
            D.build(replace(D.Design(), token_bits=3, phase_bits=7))
        with self.assertRaises(ValueError):
            D.build(replace(D.Design(), family='predictive'))

    def test_sync_regression_cannot_be_hidden_by_sinad_gain(self):
        def row(picture=.98, sinad=5., cnr=4.):
            return dict(cnr=cnr, picture=picture, sinad=sinad,
                        baseline=dict(picture=.98, sinad=4.))
        rows = [row() for _ in range(20)]
        self.assertTrue(D.assess(rows)['eligible'])
        rows.append(row(picture=.25, sinad=12.))
        result = D.assess(rows)
        self.assertFalse(result['eligible'])
        self.assertGreater(result['weak_mean_gain_db'], 1.)
        self.assertTrue(any('displayed lines' in s for s in result['violations']))
        rows[-1] = row(picture=.98, sinad=3., cnr=25.)
        self.assertFalse(D.assess(rows)['eligible'])
        rows[-1] = row(sinad=float('nan'))
        self.assertFalse(D.assess(rows)['eligible'])

    def test_heldout_seeds_are_disjoint(self):
        screen = {r['seed'] for r in D.case_specs('screen')}
        final = {r['seed'] for r in D.case_specs('validation')}
        self.assertFalse(screen & final)
        self.assertEqual(len(D.case_specs('validation')), 56)


if __name__ == '__main__':
    unittest.main()
