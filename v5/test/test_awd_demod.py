"""AWD6 program/emulator invariants; C5VRX by Twotoz and the contributors."""
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'research'))
import bs_emu  # noqa: E402
import gen_awd_demod as G  # noqa: E402
import sim  # noqa: E402

PROGRAM = ROOT / 'main' / 'awd6.bsasm'


def instructions(text):
    return [l.strip() for l in text.splitlines() if l.strip() and not l.startswith(('#', 'lut ', 'cfg '))]


class EmulatorTest(unittest.TestCase):
    """The emulator must reproduce programs whose behaviour is established."""

    def test_u85q_programs_match_the_bit_exact_model(self):
        raw = np.random.default_rng(1).integers(0, 256, 60000).astype(np.uint8)
        for name in ['u85q', 'u85q_kp060', 'u85q_kp050', 'u85g']:
            path = ROOT / 'main' / (name + '.bsasm')
            emu = bs_emu.run(path.read_text(), raw) & 63
            ref = sim._pair_codes(raw, sim.u85q_lut(path), sim.PAIR_BITS, 5)
            np.testing.assert_array_equal(emu[4:-4], ref[4:-4], err_msg=name)

    def test_counter_probe_matches_its_board_result(self):
        # main/bs_addctia_probe.bsasm reported PASS on an ESP32-C5 (C5VRX-3,
        # tools/bs_addctia_probe.md) with this exact input and oracle.
        probe = ROOT.parent / 'main' / 'bs_addctia_probe.bsasm'
        if not probe.exists():
            self.skipTest('C5VRX-3 probe not in this checkout')
        data = np.zeros(256, np.uint8)
        for pair in range(128):
            data[2 * pair] = (256 - ((pair * 7 + 3) & 31)) & 255
            data[2 * pair + 1] = (pair * 13 + 19) & 31
        out = bs_emu.run(probe.read_text(), data)
        for pair in range(1, 128):
            src = pair - 1
            expected = (((src * 13 + 19) & 31) - ((src * 7 + 3) & 31)) % 32
            self.assertEqual(out[2 * pair], expected)
            self.assertEqual(out[2 * pair + 1], expected)


class Awd6Test(unittest.TestCase):
    def test_checked_in_program_is_generated(self):
        self.assertEqual(PROGRAM.read_text(), G.program(G.build()))

    def test_program_equals_plain_model_after_startup(self):
        rng = np.random.default_rng(7)
        raw = rng.integers(0, 256, 120000).astype(np.uint8)
        np.testing.assert_array_equal(bs_emu.run(PROGRAM.read_text(), raw)[16:-8] & 63, G.model(raw)[16:-8])
        c = sim.make_case(9101, 6., 'NTSC', 1.7, filter_hz=16e6, fields=1, quantizer='ultrafine')
        raw = c['raw'][:200000]
        np.testing.assert_array_equal(bs_emu.run(PROGRAM.read_text(), raw)[16:-8] & 63, G.model(raw)[16:-8])

    def test_schedule_resources(self):
        ins = instructions(PROGRAM.read_text())
        bundles = re.findall(r'^(controller|worker)_\d+:', PROGRAM.read_text(), re.M)
        self.assertEqual(len(bundles), 8)
        self.assertEqual(ins.count('read 16,'), 4)
        self.assertEqual(ins.count('write 16,'), 4)
        self.assertEqual(ins.count('addctiah'), 4)
        self.assertEqual(ins.count('ldctiah'), 4)
        text = PROGRAM.read_text()
        # LUT16 maps L over B0..B15: counter B must never be a source. Input
        # bits stay below 32 (REG_MEM1 cannot be combined with counters).
        self.assertNotRegex(text, r'\bB\d+')
        for m in re.finditer(r'set \S+ (\d+)\.\.(\d+),', text):
            self.assertLess(int(m.group(2)), 32)
        # Operand bits 24..25 of ADDCTIAH are zero: no carry into phase bits.
        self.assertEqual(text.count('set 24..25 L,'), 4)

    def test_lut_fields(self):
        lut = G.build().astype(np.int64)
        t = G.theta6(np.arange(256))
        np.testing.assert_array_equal(lut[:256] & 63, t)
        np.testing.assert_array_equal((lut[:256] >> 9) & 63, (-t) % 64)
        # The level stored by every word (AWD next level or the theta words'
        # recovery level) has high bits != 0: it never addresses the theta bank.
        self.assertTrue(np.all(((lut >> 7) & 3) != 0))
        v = G.levels()
        e, nxt = G.transition(v, G.DEFAULT['b'])
        for parity in (0, 1):
            for li in range(G.LEVELS):
                l0, lh = G.level_bits(li)
                d = np.arange(64)
                words = lut[d | (parity << 6) | (l0 << 7) | (lh << 8)]
                delta = d if parity == 0 else (-d) % 64
                np.testing.assert_array_equal(words & 63, G.dac(v[li] + e[li, delta]))

    def test_steady_carrier_is_reproduced_from_every_level(self):
        v = G.levels()
        e, nxt = G.transition(v, G.DEFAULT['b'])
        code = G.dac(v[:, None] + e)
        for d in range(64):
            freq = G.wrap(d / 64)
            if not (-6.4e6 < freq / G.DT < 10e6):     # DAC window and beyond the top
                continue
            for start in range(G.LEVELS):
                li = start
                for _ in range(64):
                    li = nxt[li, d]
                self.assertEqual(code[li, d], G.dac(freq), (d, start))

    def test_extremes_after_long_opposite_extremes(self):
        """Long sync-level stretches followed by white, and the reverse, on
        wide and off-centre VTXs must come out at the right level (a centre
        left at one extreme must not lock the other onto the wrong branch)."""
        for dev in (1.0, 1.4, 1.7, 2.0):
            for offset in (-1.5e6, 0., 1.5e6):
                porch = -1e6 + offset
                sync = porch - 40 / 140 * 6.7e6 * dev
                white = porch + 100 / 140 * 6.7e6 * dev
                f = np.repeat([sync, white, sync, porch, white, sync], 8000)
                z = 4.2 * np.exp(2j * np.pi * np.cumsum(np.repeat(f, 2)) / 40e6)
                out = bs_emu.run(PROGRAM.read_text(), sim.quantize_iq(z, 'ultrafine')) & 63
                expect = G.dac(f[::8000] * G.DT)
                got = [int(np.median(out[k * 16000 + 12000:k * 16000 + 15000])) for k in range(6)]
                np.testing.assert_allclose(got, expect, atol=1, err_msg=f'{dev} {offset}')

    def test_escape_moves_toward_the_middle(self):
        v = G.levels()
        e, nxt = G.transition(v)
        mid = G.DEFAULT['mid']
        for li in range(G.LEVELS):
            for d in range(64):
                if v[li] < mid and e[li, d] < -G.DEFAULT['esc']:
                    self.assertEqual(nxt[li, d], li + 1)
                if v[li] > mid and e[li, d] > G.DEFAULT['esc']:
                    self.assertEqual(nxt[li, d], li - 1)

    def test_official_assembler_accepts_program(self):
        idf = os.environ.get('IDF_PATH')
        if not idf:
            self.skipTest('IDF_PATH not set')
        caps = Path(idf) / 'components' / 'esp_driver_bitscrambler' / 'bsasm_targets' / 'esp32c5.json'
        r = subprocess.run([sys.executable, str(Path(idf) / 'tools' / 'bsasm.py'), str(PROGRAM), os.devnull,
                            '-c', str(caps)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
