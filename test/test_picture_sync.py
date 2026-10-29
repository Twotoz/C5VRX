#!/usr/bin/env python3
"""C5VRX by Twotoz and the C5VRX contributors: main/picture_sync.h, the
flight log's goggle-like picture measure, on simulated IQ windows (sim.py,
physical ADC, NTSC) through both decoders' LUTs: a strong carrier keeps its
syncs, receiver noise has none, and every window agrees with an independent
NumPy reference to one pulse."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))
import sim  # noqa: E402
import p64_model  # noqa: E402

WINDOW = 8190          # control.c AFC_WINDOW_BYTES
LINE = 63.556 * 20

HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include "picture_sync.h"
int main(int argc, char **argv)
{
    if (argc < 3) return 2;
    static uint16_t lut[1024];
    static uint8_t raw[8190];
    FILE *f = fopen(argv[1], "rb");
    if (!f || fread(lut, 2, 1024, f) != 1024) return 2;
    const unsigned bits = (unsigned)atoi(argv[2]);
    picture_state_t st = {0};
    int first = 1;
    while (fread(raw, 1, sizeof(raw), f) == sizeof(raw)) {
        /* The slicer comes from the window before: the first window is not
         * counted, and here every window primes it from itself first, so the
         * result compares with the per-window reference. */
        const picture_sync_t primed = picture_sync_window(&st, raw, sizeof(raw), lut, bits);
        if (first && (primed.found || primed.expected)) return 3;
        first = 0;
        const picture_sync_t r = picture_sync_window(&st, raw, sizeof(raw), lut, bits);
        printf("%u %u\n", r.found, r.expected);
    }
    return 0;
}
'''


def reference(raw, table, bits):
    """The research prototype: same rule in NumPy (percentiles interpolated)."""
    codes = sim._pair_codes(np.asarray(raw, np.uint8), table, sim.PAIR_BITS, bits)[64::2].astype(float)
    y = np.convolve(codes, np.ones(4), mode='valid')
    tip, mid = np.percentile(y, 3), np.median(y)
    expected = max(1, int(len(y) * 10 // 12711))
    if mid - tip < 20:
        return 0, expected
    low = 2 * y < tip + mid
    found, last = 0, -10 ** 9
    for s in np.flatnonzero(np.diff(low.astype(np.int8)) == 1) + 1:
        run = np.argmax(~low[s:]) if (~low[s:]).any() else len(low) - s
        if run >= 40 and s - last > 0.7 * LINE:
            found += 1
            last = s
    return min(found, expected), expected


def windows(cn, deviation, seed, count=12):
    c = sim.make_case(seed, cn, 'NTSC', deviation, filter_hz=12e6, fields=1, physical_adc=True, adc_gain=3.5)
    raw = np.asarray(c['raw'], np.uint8)
    step = (len(raw) - WINDOW) // count
    return [raw[k * step: k * step + WINDOW] for k in range(count)]


class PictureSyncTest(unittest.TestCase):
    def run_c(self, table, bits, wins):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'h.c').write_text(HARNESS)
            subprocess.run([os.environ.get('CC', 'cc'), '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(ROOT / 'main'), str(d / 'h.c'), '-o', str(d / 'h')], check=True)
            (d / 'in.bin').write_bytes(np.asarray(table, np.uint16).tobytes() + b''.join(w.tobytes() for w in wins))
            out = subprocess.run([str(d / 'h'), str(d / 'in.bin'), str(bits)], check=True,
                                 capture_output=True, text=True).stdout.split('\n')
        return [tuple(map(int, line.split())) for line in out if line.strip()]

    def test_decoders_and_levels(self):
        for name, table, bits in (('P64', p64_model.repaired_table(), 4), ('U85Q', sim.u85q_lut(), 5)):
            for deviation in (0.66, 1.0):
                strong = windows(20, deviation, 701)
                c = self.run_c(table, bits, strong)
                share = sum(f for f, _ in c) / sum(e for _, e in c)
                self.assertGreaterEqual(share, 0.9, (name, deviation, c))
                for w, (f, e) in zip(strong, c):
                    rf, re_ = reference(w, table, bits)
                    self.assertEqual(e, re_)
                    self.assertLessEqual(abs(f - rf), 1, (name, deviation))
            rng = np.random.default_rng(5)
            noise = [rng.integers(0, 256, WINDOW, dtype=np.uint8) for _ in range(8)]
            c = self.run_c(table, bits, noise)
            self.assertLessEqual(sum(f for f, _ in c) / sum(e for _, e in c), 0.1, name)
            weak = self.run_c(table, bits, windows(-3, 0.66, 702))
            self.assertLess(sum(f for f, _ in weak) / sum(e for _, e in weak), share, name)
        print('PASS: picture sync (P64 and U85Q, deviation 0.66/1.0): strong >= 90 %, noise <= 10 %, '
              'weak lower, each window within one pulse of the NumPy reference')


if __name__ == '__main__':
    unittest.main()
