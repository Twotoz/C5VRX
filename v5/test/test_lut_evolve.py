"""Unrestricted shared-LUT search invariants for C5VRX."""
from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'research'))
import lut_evolve as E


class LutEvolveTest(unittest.TestCase):
    def test_arbitrary_words_and_input_bits_are_supported(self):
        rng = np.random.default_rng(71)
        for tb in (4, 5, 6):
            g = E.Genome(tb, [0, 2, 4, 6, 8, 10, 12, 14, 1, 3],
                         rng.integers(65536, size=1024, dtype=np.uint16))
            self.assertEqual(E.Genome.restore(g.record()).id, g.id)
            program = E.synthesize(g)
            for j, bit in enumerate(g.bits):
                self.assertIn(f'set {16+j} {bit},', program)
            self.assertEqual(program.count('read 16,'), 4)
            self.assertEqual(program.count('write 16,'), 4)
            self.assertEqual(g.lut.nbytes, 2048)

    def test_state_and_token_relabeling_preserve_behavior(self):
        rng = np.random.default_rng(73)
        raw = rng.integers(256, size=4096, dtype=np.uint8)
        for tb in (4, 5, 6):
            g = E.Genome(tb, rng.choice(16, 10, replace=False).tolist(),
                         rng.integers(65536, size=1024, dtype=np.uint16))
            nt, ns = 1 << tb, 1 << (10 - tb)
            new = E.relabel(g, np.r_[0, rng.permutation(np.arange(1, ns))], rng.permutation(nt))
            def decode(x):
                return E.D.sim._pair_codes(raw, x.lut, np.array(x.bits), x.token_bits)
            np.testing.assert_array_equal(decode(g), decode(new))

    def test_mutations_reach_all_fields_and_new_input_subsets(self):
        g = E.seeded(E.D.Design())
        rng = np.random.default_rng(79)
        changes = [False] * 4
        for _ in range(200):
            child = E.mutate(g, rng)
            changes[0] |= bool(np.any((child.lut & 63) != (g.lut & 63)))
            changes[1] |= bool(np.any((child.lut & 1984) != (g.lut & 1984)))
            changes[2] |= bool(np.any((child.lut >> 11) != (g.lut >> 11)))
            changes[3] |= set(child.bits) != set(g.bits)
            self.assertEqual(len(set(child.bits)), 10)
            self.assertEqual(child.lut.dtype, np.uint16)
        self.assertTrue(all(changes))

    def test_reject_invalid_or_corrupted_genomes(self):
        g = E.seeded(E.D.Design())
        with self.assertRaises(ValueError):
            E.Genome(3, g.bits, g.lut)
        with self.assertRaises(ValueError):
            E.Genome(5, [0] * 10, g.lut)
        record = g.record()
        record['lut'][7] ^= 1
        with self.assertRaises(ValueError):
            E.Genome.restore(record)

    def test_new_holdout_not_reused_from_previous_run(self):
        screen = {r['seed'] for r in E.case_specs('screen')}
        validation = {r['seed'] for r in E.case_specs('validation')}
        previous = {r['seed'] for r in E.D.case_specs('screen') + E.D.case_specs('validation')}
        self.assertFalse(screen & validation)
        self.assertFalse((screen | validation) & previous)


if __name__ == '__main__':
    unittest.main()
