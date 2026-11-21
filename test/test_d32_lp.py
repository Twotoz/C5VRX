"""C5VRX/Twotoz: ESP-IDF assembled D32-R2-LP against independent integers.
Host assertions do not verify physical timing, throughput, colour or RF range.
"""
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'research'))
import d32_lp
import nl_tracker
import sim
import u85q_lp


def reference(raw, table):
    # Explicit lookup/state machine, no reuse of sim._pair_codes or lp_codes.
    state, previous_x, y = 0, 0, 0
    stock, filtered = [], []
    for a, b in zip(raw[::2], raw[1::2]):
        stock.extend((previous_x, previous_x))
        filtered.extend((y, y))
        y = (y // 2) + (previous_x // 2)
        address = (int(a) >> 4) | ((int(a) & 15) << 4) | ((int(b) >> 7) << 8) | (((int(b) >> 3) & 1) << 9)
        word = int(table[(state << 5) | (int(table[address]) >> 11)])
        previous_x, state = word & 63, (word >> 6) & 31
    return np.array(stock, np.uint8), np.array(filtered, np.uint8)


class D32LP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = sim.u85q_lut(ROOT / 'main/d32.bsasm')

    def check_stream(self, raw, table=None, program=None):
        table = self.table if table is None else table
        stock, expected = reference(raw, table)
        n = len(raw) - 8   # input prefetch; no synthetic EOF read
        got = np.frombuffer(u85q_lp.emulate(program or d32_lp.OUT, raw.tobytes(), n), np.uint8) & 63
        np.testing.assert_array_equal(got, expected[:n])
        np.testing.assert_array_equal(got[::2], got[1::2])
        np.testing.assert_array_equal(d32_lp.codes(raw, table)[:n], expected[:n])
        if program is None:
            direct = np.frombuffer(u85q_lp.emulate(ROOT / 'main/d32.bsasm', raw.tobytes(), n), np.uint8) & 63
            np.testing.assert_array_equal(direct, stock[:n])
        self.assertLessEqual(int(got.max()), 62)

    def test_generated_loader_and_cadence(self):
        self.assertEqual(d32_lp.OUT.read_text(), d32_lp.generate(False))
        inst, meta, lut = u85q_lp.assemble(d32_lp.OUT)
        self.assertEqual(len(inst), 8)
        np.testing.assert_array_equal(lut, self.table)
        np.testing.assert_array_equal(lut, nl_tracker.table())
        binary = u85q_lp._bsasm().insts_to_binary(inst, meta, lut)
        self.assertEqual(binary[:7], bytes((1, 0, 3, 8, 0, 2, 1)))
        self.assertEqual(len(binary), 2348)
        for k in range(0, 8, 2):
            self.assertEqual([inst[k].get('read', 0), inst[k+1].get('read', 0)], [16, 0])
            self.assertEqual([inst[k].get('write', 0), inst[k+1].get('write', 0)], [8, 8])
            self.assertEqual(inst[k]['op']['op'], u85q_lp.bsasm_op('LDCTI'))
            self.assertEqual(inst[k+1]['op']['op'], u85q_lp.bsasm_op('ADDCTI'))
        # Keep stock generated sources unchanged (not just equal mean transfer).
        self.assertEqual(nl_tracker.PROGRAM.read_text(), nl_tracker.program_text(self.table))
        self.assertEqual(nl_tracker.HEADER.read_text(), nl_tracker.header_text(self.table))

    def test_exhaustive_raw_token_routing(self):
        # All 65536 raw pairs: setting B sign address bits to zero cannot alter
        # the first lookup's token; the second LUT/state transition is unchanged.
        for word in range(65536):
            address = sum(((word >> int(b)) & 1) << k for k, b in enumerate(sim.PAIR_BITS))
            self.assertEqual(int(self.table[address]) >> 11, int(self.table[address & 255]) >> 11)
        raw = np.arange(65536, dtype='<u2').view(np.uint8)
        self.check_stream(raw)
        # Physical ring and descriptor boundaries never reset phase/filter.
        self.check_stream(np.random.default_rng(3202).integers(0, 256, 4*32768, dtype=np.uint8))

    def test_prefilter_state_and_legacy_carry_regression(self):
        raw = np.random.default_rng(202632).integers(0, 256, 8192, dtype=np.uint8)
        trace = []
        u85q_lp.emulate(d32_lp.OUT, raw.tobytes(), len(raw)-8, trace=trace)
        state = 0
        for k, (_, _, value, _) in enumerate(trace[1::2]):
            a, b = map(int, raw[2*k:2*k+2])
            address = (a>>4) | ((a&15)<<4) | ((b>>7)<<8) | (((b>>3)&1)<<9)
            expected = int(self.table[(state<<5) | (int(self.table[address])>>11)])
            self.assertEqual(value, expected)   # DAC, next phase AND shared token bits
            state = (expected>>6)&31
        # Merely putting the D32 LUT in historical U85Q-LP introduces a carry.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'legacy.bsasm'
            path.write_text('cfg prefetch true\ncfg eof_on downstream\ncfg trailing_bytes 0\ncfg lut_width_bits 16\n'
                            +u85q_lp.program()+'lut '+' '.join(map(str,self.table))+'\n')
            legacy = np.frombuffer(u85q_lp.emulate(path,raw.tobytes(),len(raw)-8),np.uint8)&63
            self.assertTrue(np.any(legacy != reference(raw,self.table)[1][:len(legacy)]))

    def test_pal_ntsc(self):
        for standard in ('NTSC', 'PAL'):
            for seed in (32, 2026):
                case = sim.make_case(seed, 2., standard, 1.4, filter_hz=12e6, fields=1)
                self.check_stream(case['raw'][:16000])
                self.check_stream(case['clean'][:16000])

    def test_rounding_full_scale_and_output_scaling(self):
        rng = np.random.default_rng(6)
        raw = rng.integers(0, 256, 512, dtype=np.uint8)
        src = d32_lp.OUT.read_text().split('lut ')[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.bsasm'
            # Exhaust all six-bit DAC inputs; constant 63 must settle to 61,
            # not 63. Separately truncated halves have a DC deadband/bias.
            for x in range(64):
                lut = (self.table & np.uint16(0xffc0)) | x
                path.write_text(src + 'lut ' + ' '.join(map(str, lut)) + '\n')
                self.check_stream(raw, lut, path)
            # All OUTPUT multipliers and affine transforms alter only low DAC
            # bits; testing arbitrary low-six-bit substitutions covers that path.
            for _ in range(4):
                lut = (self.table & np.uint16(0xffc0)) | rng.integers(0, 64, 1024, dtype=np.uint16)
                path.write_text(src + 'lut ' + ' '.join(map(str, lut)) + '\n')
                self.check_stream(np.tile(raw, 4), lut, path)
        inp = np.full(128, 63, np.uint8)
        out = d32_lp.filter_codes(inp)
        self.assertEqual(list(out[::2][:8]), [0, 31, 46, 54, 58, 60, 61, 61])
        # Distinguish separate shifts from (y+x)>>1 (odd+odd).
        self.assertEqual(int(d32_lp.filter_codes(np.full(128, 3, np.uint8))[-1]), 1)


if __name__ == '__main__':
    unittest.main()
