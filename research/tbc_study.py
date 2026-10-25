"""C5VRX by Twotoz and the C5VRX contributors: line TBC study (host model).

Runs the firmware's own main/sync_tbc.h (compiled here through a small C
shim) on research/sim.py cases (physical 4-bit ADC model, 12 MHz filter)
and the real U80P64-R1 LUT, then measures what a decoder without a
time-base corrector (HDZero: TP2825) line-locks on:

- leading-edge timing of every normal line on the P64 output (5 MHz
  goggle filter, interpolated 50 % crossing), as deviation from a straight
  line grid (rms ns and share of lines more than 100 ns off);
- colour: burst amplitude and phase per line and the picture, with and
  without the TBC, on the noise-free input (must be identical);
- safety: bytes changed in burst, picture or vertical-interval pulses.

Synthetic engineering numbers, not goggle acceptance or measured range.
The sim NTSC raster places lines on a 4-sample grid, so NTSC timing is
reported against a straight-line fit, never against that raster.

  python research/tbc_study.py            # writes research/data/tbc_study.json
"""
import ctypes
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy import signal as sg

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import sim  # noqa: E402
import p64_model  # noqa: E402

SHIM = r'''
#include "sync_tbc.h"
#include "phase8_gain_lut.h"
#include <stdlib.h>
static tbc_tables_t T;
unsigned run(uint8_t *raw, long n, double period, int ref_sync, int ref_porch)
{
    static int init; if (!init) { tbc_tables_init(&T, c5vrx_phase8_gain_lut); init = 1; }
    size_t size = 1; while ((long)size < n + 1024) size <<= 1;
    uint8_t *ring = calloc(size, 1); memcpy(ring, raw, n);
    tbc_t s; tbc_reset(&s, (int32_t)(period * 65536.0 + 0.5)); tbc_set_reference(&s, ref_sync, ref_porch);
    tbc_ring_t r = {.ring = ring, .mask = (uint32_t)(size - 1)};
    for (long c = 4092; ; c += 4092) {
        r.complete = c < n ? c : n; r.write_min = r.complete - 24576 + 8188;
        for (int k = 0; k < 4; ++k) tbc_run(&T, &s, &r, 6);
        if (c >= n) break;
    }
    memcpy(raw, ring, n); free(ring);
    return s.rewritten;
}
'''
TABLE = np.array(p64_model.checked_in_table(), np.uint16)
LP = sg.butter(4, 5e6, fs=40e6, output='sos')


def library(directory):
    src = Path(directory) / 'shim.c'
    src.write_text(SHIM)
    lib = Path(directory) / 'libtbc.so'
    subprocess.check_call(['cc', '-O2', '-std=c11', '-shared', '-fPIC', '-I', str(ROOT / 'main'), str(src),
                           '-lm', '-o', str(lib)])
    so = ctypes.CDLL(str(lib))
    so.run.restype = ctypes.c_uint
    return so


def tbc(lib, raw, period, ref_sync, ref_porch):
    out = np.ascontiguousarray(raw.copy())
    n = lib.run(out.ctypes.data_as(ctypes.c_void_p), ctypes.c_long(len(out)), ctypes.c_double(period),
                ctypes.c_int(ref_sync), ctypes.c_int(ref_porch))
    return out, int(n)


def volts(raw):
    return sg.sosfilt(LP, sim.DAC_VOLTS[sim._pair_codes(raw, TABLE, sim.PAIR_BITS, 4)])


def truth_edges(truth):
    starts = np.flatnonzero(np.diff((truth < -20).astype(int)) == 1)
    out = []
    for h in starts:
        seg = truth[h - 30:h + 30]
        i = np.flatnonzero((seg[:-1] >= -20) & (seg[1:] < -20))
        if len(i):
            out.append(h - 30 + i[0] + (seg[i[0]] + 20) / (seg[i[0]] - seg[i[0] + 1]))
    return np.array(out)


def grid_jitter(y, normal, threshold, period):
    edges = []
    for p in normal + 18:
        a = int(p) - 10
        s = y[a:a + 100] - threshold
        j = np.flatnonzero((s[:-1] >= 0) & (s[1:] < 0))
        edges.append(a + j[0] + s[j[0]] / (s[j[0]] - s[j[0] + 1]) if len(j) else np.nan)
    e = np.array(edges)
    ok = np.isfinite(e)
    n = np.round((normal - normal[0]) / period)[ok]
    fit = np.polyfit(n, e[ok], 1)
    d = e[ok] - np.polyval(fit, n)
    return float(np.std(d[abs(d) < 40]) * 25), float(np.mean(abs(d) > 4) * 100)


def burst(y, case, lag):
    region = case['region']
    fsc = 4433618.75 if case['standard'] == 'PAL' else 3579545.4545
    idx = np.flatnonzero(region == 3)
    starts = idx[np.r_[True, np.diff(idx) > 1]][5:]
    z = np.array([np.sum(y[np.arange(s + 10, s + 80) + lag] * np.exp(-2j * np.pi * fsc * np.arange(s + 10, s + 80) / 40e6))
                  for s in starts])
    return np.abs(z), np.angle(z)


CASES = [('PAL', 1.0, 0.0, 30), ('PAL', 1.0, 0.0, 12), ('PAL', 1.0, 0.0, 10), ('PAL', 1.0, 0.0, 8),
         ('PAL', 0.69, 0.0, 12), ('PAL', 1.7, 0.0, 12), ('PAL', 1.0, 400e3, 12), ('PAL', 1.0, 0.0, 3),
         ('NTSC', 1.0, 0.0, 30), ('NTSC', 1.0, 0.0, 12), ('NTSC', 1.0, 0.0, 8), ('NTSC', 0.69, -300e3, 10)]


def main():
    rows = []
    with tempfile.TemporaryDirectory() as directory:
        lib = library(directory)
        for standard, dev, offset, cn in CASES:
            case = sim.make_case(11, cn, standard, dev, filter_hz=12e6, fields=4, physical_adc=True, adc_gain=3.0,
                                 offset_hz=offset)
            period = 2560. if standard == 'PAL' else 40e6 / 15734.264
            ref = (int(-1000 + offset / 1e3 - 1914 * dev), int(-1000 + offset / 1e3))
            raw, rewritten = tbc(lib, case['raw'], period, *ref)
            clean, _ = tbc(lib, case['clean'], period, *ref)
            region = case['region']
            y0, y1 = volts(case['raw']), volts(raw)
            c0, c1 = volts(case['clean']), volts(clean)
            lag = sim.calibrate(c0, case['truth'], region)[0]
            a0, p0 = burst(c0, case, lag)
            a1, p1 = burst(c1, case, lag)
            picture = np.flatnonzero(region == 1)
            picture = picture[picture + lag < len(c0)]
            edges = truth_edges(case['truth'])
            normal = np.array([t for t in edges if t > 4000 and region[int(t) + 230] == 3])
            tip = np.median(y0[np.flatnonzero(region == 2)[400:] + 20])
            porch = np.median(y0[np.flatnonzero(region == 3)[400:] - 30])
            j0, b0 = grid_jitter(y0, normal, (tip + porch) / 2, period)
            j1, b1 = grid_jitter(y1, normal, (tip + porch) / 2, period)
            v_pulses = [t for t in edges if region[int(t) + 230] != 3]
            row = dict(standard=standard, deviation=dev, offset_khz=offset / 1e3, cn_db=cn,
                       lines=len(normal), rewritten=rewritten,
                       edge_jitter_ns=[round(j0, 1), round(j1, 1)], lines_over_100ns_pct=[round(b0, 2), round(b1, 2)],
                       burst_phase_change_max_deg=round(float(np.max(abs(np.degrees(np.angle(np.exp(1j * (p1 - p0))))))), 3),
                       burst_amplitude_change_max_pct=round(float(np.max(abs(a1 / a0 - 1)) * 100), 3),
                       picture_change_max_v=round(float(np.max(abs(c1[picture + lag] - c0[picture + lag]))), 5),
                       v_pulses_touched=int(sum(np.any(raw[int(t) - 45:int(t) + 400] != case['raw'][int(t) - 45:int(t) + 400])
                                                for t in v_pulses)))
            rows.append(row)
            print(row, flush=True)
    out = HERE / 'data' / 'tbc_study.json'
    out.write_text(json.dumps({'evidence': 'host model: research/sim.py physical 4-bit ADC, real P64 LUT, '
                                           'firmware main/sync_tbc.h; not goggle or board acceptance',
                               'rows': rows}, indent=1) + '\n')
    print('wrote', out)


if __name__ == '__main__':
    main()
