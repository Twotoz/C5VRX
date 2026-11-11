"""C5VRX by Twotoz and the C5VRX contributors: AUTO OUTPUT level consistency.

Acceptance run for the invariant of PR #302's review: the analog CVBS
reference levels (sync tip, blanking, sync depth, peak white) stay the same
whatever the RF level, the gain row, the VTX deviation or the decoder; the
picture may get noisier, the levels may not follow the reception.

Extends AUTO OUTPUT (main/output_auto.h, PR #301/#302), research/output_level.py
(issue #200) and research/output_tip_headroom.py. The firmware's own code
decides: a C harness built from main/ runs afc2_measure (afc.h),
picture_sync_window (picture_sync.h) and output_auto_observe (output_auto.h)
with the gates of control.c output_auto_service on 8190-byte control windows
of simulated IQ (sim.py, physical ADC, fixed ultrafine lane), and
output_entry (output_level.h) maps the noise-free decode of the same VTX to
the DAC. Reported levels are DAC codes of that noise-free decode after the
goggle's 5 MHz low-pass, and model volts into 75 ohm (output_level.py VOLTS:
3.3 V GPIO, nominal resistors, 200 ohm series; not a measured voltage).

Scenarios, each one AUTO OUTPUT context (one channel, one decoder):
  strong    learn on a strong carrier (C/N 30 dB);
  walk      strong, then a walk down to C/N 0 dB and back, at another ADC
            radius per step (the gain row changes): the level of every step;
  cold      first learned at a weak C/N (a boot at the range edge), 30 s of
            windows: the level it reaches, or none (the 1.0x level stays
            when there is no trustworthy sync to learn from).
One VTX with more picture swing than the standard (issue #200 levels) checks
the DAC bound: its white must stay on the DAC and its level must hold too; it
is left out of the comparison across VTXs. Learned weak, its white peak is
under-read until a strong batch arrives (reported as clipped_wide_white_cold_max,
not gated: that level is provisional).

    python research/output_consistency.py            print the tables
    python research/output_consistency.py --write    rewrite research/data/output_consistency.json
    python research/output_consistency.py --check    the acceptance limits hold and the data file is current

Host model only: no goggle, voltage, 75 ohm load or range is measured here.
"""
import json
import math
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import scipy.signal as sg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'research'))
import iq_lane_fusion as F  # noqa: E402
import output_level  # noqa: E402
import p64_model  # noqa: E402
import sim  # noqa: E402
import u50p64  # noqa: E402

OUT = ROOT / 'research/data/output_consistency.json'
WINDOW = 8190                 # control.c AFC_WINDOW_BYTES
WINDOW_US = 55000             # ~18 control windows a second
SEED = 5201
STRONG_CN = 30
STRONG_WINDOWS = 192          # 10 s
STEP_WINDOWS = 64             # four batches per walk step
WALK = ((20, 4.2), (12, 3.0), (8, 5.5), (6, 4.2), (4, 3.0), (3, 5.5), (2, 4.2), (1, 4.2), (0, 4.2),
        (2, 3.0), (4, 5.5), (8, 4.2), (20, 3.0), (30, 4.2))          # (C/N dB, ADC radius in cells)
COLD_CNS = (12, 8, 6, 4, 2)
COLD_WINDOWS = 540            # 30 s
ISSUE200_WHITE = 5.2e6 / (.69 * sim.DEV_PP * 100 / 140)   # output_level.py issue200_levels
CASES = (('NTSC', 0.5, 1.0), ('NTSC', 0.7, 1.0), ('NTSC', 1.0, 1.0), ('PAL', 0.7, 1.0), ('PAL', 1.0, 1.0),
         ('PAL', 1.3, 1.0), ('NTSC', 0.69, ISSUE200_WHITE))   # standard, deviation, white scale
DECODERS = ('U80P64-R1', 'U50P64-R1')
U50_MAX_DEVIATION = 1.10      # control.c demod_deviation_guard: U50 falls back to U80 from here

# Acceptance limits, DAC codes (63 codes are the DAC range).
LIMIT_WALK_CODES = 1.0        # any level within one context, strong against every walk step
LIMIT_ACROSS_CODES = 2.0      # sync tip, blanking and sync depth across deviations and decoders
LIMIT_COLD_CODES = 2.0        # a level first learned weak against the strong one
LIMIT_COLD_LEARNED_CN = 8     # a cold start learns the level from this C/N (dB) on, every VTX
LIMIT_CLIPPED = 0.01          # share of the noise-free output on code 0 or 63

HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include "afc.h"
#include "phase8_gain_lut.h"
#include "picture_sync.h"
#include "output_auto.h"
/* stdin: 1024 LUT words, then records: 'R' (context reset) or 'W' + 8190 raw
 * bytes. One line per window: the gates and evidence of control.c
 * output_auto_service and the word that is live after it. */
int main(int argc, char **argv)
{
    if (argc < 2) return 2;
    const unsigned bits = (unsigned)atoi(argv[1]);
    static uint16_t lut[1024];
    static uint8_t raw[8190];
    if (fread(lut, 2, 1024, stdin) != 1024) return 2;
    output_auto_t a;
    picture_state_t pic = {0};
    output_auto_reset(&a);
    int64_t now = 1000000;
    for (int tag; (tag = getchar()) != EOF;) {
        if (tag == 'R') { output_auto_reset(&a); pic = (picture_state_t){0}; continue; }
        if (tag != 'W' || fread(raw, 1, sizeof(raw), stdin) != sizeof(raw)) return 3;
        now += WINDOW_US;
        const bool window_ok = afc2_envelope_stationary(raw, sizeof(raw));
        const picture_sync_t ps = picture_sync_window(&pic, raw, sizeof(raw), lut, bits);
        afc2_result_t r = {0};
        if (window_ok) r = afc2_measure(raw, sizeof(raw), c5vrx_phase8_gain_lut);
        const bool valid = window_ok && r.lines && r.standard && r.porch_pairs && r.sync_pairs;
        int used = 0;
        unsigned depth_x4 = 0;
        if (valid && r.polarity < 0 && pic.levels && ps.expected && ps.found * 5u >= ps.expected * 4u &&
            r.porch_khz - r.sync_khz > 0) {
            depth_x4 = (unsigned)((r.porch_khz - r.sync_khz) * 252 / 15000);
            used = 1;
            unsigned word;
            if (output_auto_observe(&a, output_auto_code_x4(r.sync_khz), pic.top_x4, depth_x4, now, &word)) output_auto_applied(&a, word, now);
        } else output_auto_miss(&a);
        printf("%d %u %u %u %u %lu %lu %lu\n", used, a.word, depth_x4, output_auto_code_x4(r.sync_khz), (unsigned)pic.top_x4,
               (unsigned long)a.batches, (unsigned long)a.rejected, (unsigned long)a.applied);
    }
    return 0;
}
'''


def build(tmp):
    src, exe = Path(tmp) / 'oc.c', Path(tmp) / 'oc'
    src.write_text(HARNESS)
    subprocess.run(['cc', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror', f'-DWINDOW_US={WINDOW_US}',
                    '-I', str(ROOT / 'main'), str(src), '-lm', '-o', str(exe)], check=True)
    return exe


def policy(exe, table, records):
    """records: 'R' or a window (uint8 array). One result row per window."""
    blob = b''.join([struct.pack('<1024H', *[int(x) for x in table])] +
                    [b'R' if isinstance(r, str) else b'W' + r.tobytes() for r in records])
    out = subprocess.run([str(exe), '4'], input=blob, stdout=subprocess.PIPE, check=True).stdout.decode().split('\n')
    return [tuple(int(x) for x in line.split()) for line in out if line]


class Vtx:
    """One VTX: unit-power clean and noise IQ of two fields and its regions."""

    def __init__(self, standard, deviation, seed, white_scale=1.0):
        c = sim.make_case(seed, 30, standard, deviation=deviation, filter_hz=12e6, physical_adc=True, adc_gain=1.0,
                          white_scale=white_scale)
        clean = c['clean_adc'] / 16.0
        noise = (c['adc'] - c['clean_adc']) / 16.0
        self.clean = clean / math.sqrt(np.mean(abs(clean) ** 2))
        self.noise = noise / math.sqrt(np.mean(abs(noise) ** 2))
        self.region = c['region']
        self.rng = np.random.default_rng(seed + 1)

    def windows(self, cn, radius, count):
        """`count` control windows at C/N `cn` (dB) and carrier radius (ADC cells)."""
        starts = self.rng.integers(0, len(self.clean) - WINDOW, (count, 2))
        at = np.arange(WINDOW)
        scale = radius / math.sqrt(10 ** (cn / 10))
        raw = F.capture((self.clean[starts[:, :1] + at] * radius + self.noise[starts[:, 1:] + at] * scale) * 16.0, F.ULTRA)
        return list(raw.astype(np.uint8))

    def reference(self, table):
        """The noise-free decode (one code per 50 ns) at the nominal radius."""
        return sim._pair_codes(F.capture(self.clean * 4.2 * 16.0, F.ULTRA), table, sim.PAIR_BITS, 4)


def affine(codes, word):
    """output_level.h output_entry for an affine word; 0 is the 1.0x level."""
    if not word & 0x8000:
        return codes
    gain, tip_x4 = (word >> 6) & 63, (word & 63) << 2 | (word >> 12) & 3
    x32 = 32 * 16 + gain * (4 * codes.astype(np.int64) - tip_x4)
    return np.clip(np.floor_divide(x32 + 16, 32), 0, 63).astype(np.uint8)


def levels(vtx, ref, word):
    """Sync tip, blanking and peak white of the DAC output for one word."""
    out = affine(ref, word)
    region = vtx.region[:len(out)]
    lp = sg.sosfilt(sim.GOGGLE, out.astype(float))
    volts = sg.sosfilt(sim.GOGGLE, output_level.VOLTS[out])
    sync, porch = np.flatnonzero(region == 2)[400:-400], np.flatnonzero(region == 0)[4000:-4000]

    def at(y):
        return float(np.median(y[sync])), float(np.median(y[porch])), float(np.percentile(y[8000:-8000], 99.5))
    tip, blank, white = at(lp)
    vt, vb, vw = at(volts)
    return dict(gain=((word >> 6) & 63) / 8.0 if word & 0x8000 else 1.0, tip=round(tip, 2), blank=round(blank, 2),
                sync=round(blank - tip, 2), white=round(white, 2), sync_mv=round(1e3 * (vb - vt), 1),
                video_mv=round(1e3 * (vw - vb), 1), clipped=round(float(np.mean((out == 0) | (out == 63))), 4))


def run():
    tables = {'U80P64-R1': p64_model.checked_in_table(), 'U50P64-R1': u50p64.table()}
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        exe = build(tmp)
        for standard, deviation, white_scale in CASES:
            vtx = Vtx(standard, deviation, SEED, white_scale)
            for name in DECODERS:
                if name == 'U50P64-R1' and deviation >= U50_MAX_DEVIATION:
                    continue
                table = tables[name]
                ref = vtx.reference(table)
                records, marks = [], []
                records += vtx.windows(STRONG_CN, 4.2, STRONG_WINDOWS)
                marks.append(('strong', STRONG_CN, 4.2, len(records)))
                for cn, radius in WALK:
                    records += vtx.windows(cn, radius, STEP_WINDOWS)
                    marks.append(('walk', cn, radius, len(records)))
                for cn in COLD_CNS:
                    records.append('R')
                    records += vtx.windows(cn, 4.2, COLD_WINDOWS)
                    marks.append(('cold', cn, 4.2, len(records)))
                res = policy(exe, table, records)
                at, steps = 0, []
                for kind, cn, radius, end in marks:
                    count = end - sum(1 for r in records[:end] if isinstance(r, str))
                    part = res[at:count]
                    at = count
                    steps.append(dict(step=kind, cn_db=cn, radius_cells=radius, windows_used=sum(p[0] for p in part),
                                      windows=len(part), learned=bool(part[-1][1]), **levels(vtx, ref, part[-1][1])))
                row = dict(standard=standard, deviation=deviation, white_scale=round(white_scale, 3), decoder=name,
                           level_1x=levels(vtx, ref, 0), steps=steps)
                rows.append(row)
                print(f"{standard} dev {deviation} white x{white_scale:.2f} {name}  1.0x sync {row['level_1x']['sync']:.1f} codes",
                      flush=True)
                for s in steps:
                    print(f"  {s['step']:6s} C/N {s['cn_db']:>2} r {s['radius_cells']:.1f} used {s['windows_used']:>2}/{s['windows']}"
                          f"  gain {s['gain']:.3f}  tip {s['tip']:5.1f} blank {s['blank']:5.1f} sync {s['sync']:5.1f}"
                          f" white {s['white']:5.1f}  {s['sync_mv']:5.1f}/{s['video_mv']:5.1f} mV clip {s['clipped']:.3f}", flush=True)
    return dict(source='research/output_consistency.py',
                evidence='host model only: simulated IQ, firmware policy headers, modelled DAC into 75 ohm',
                seed=SEED, window_bytes=WINDOW, strong_cn_db=STRONG_CN, rows=rows, summary=summary(rows))


def summary(rows):
    """Spreads in DAC codes: within a context, cold against strong, and across VTXs and decoders."""
    walk = cold = clipped = clipped_wide_cold = 0.0
    strong, not_learned = [], set()
    for row in rows:
        s0 = row['steps'][0]
        if row['white_scale'] == 1.0:
            strong.append(s0)
        for s in row['steps']:
            # A VTX with more white than the standard, first learned weak: its white peak is
            # under-read until a strong batch (the level is provisional); reported, not gated.
            if row['white_scale'] != 1.0 and s['step'] == 'cold':
                clipped_wide_cold = max(clipped_wide_cold, s['clipped'])
            else:
                clipped = max(clipped, s['clipped'])
            d = max(abs(s[k] - s0[k]) for k in ('tip', 'blank', 'sync', 'white'))
            if s['step'] == 'walk':
                walk = max(walk, d)
            elif s['step'] == 'cold' and s['learned']:
                cold = max(cold, d)
            elif s['step'] == 'cold':
                not_learned.add(s['cn_db'])
    across = {k: round(max(s[k] for s in strong) - min(s[k] for s in strong), 2) for k in ('tip', 'blank', 'sync', 'white')}
    return dict(walk_max_codes=round(walk, 2), cold_max_codes=round(cold, 2), across_strong_codes=across,
                cold_learned_from_cn_db=min([cn for cn in COLD_CNS if all(c < cn or c not in not_learned for c in COLD_CNS)],
                                            default=99),
                clipped_max=clipped, clipped_wide_white_cold_max=clipped_wide_cold,
                levels_codes={k: [min(s[k] for s in strong), max(s[k] for s in strong)] for k in ('tip', 'blank', 'sync', 'white')},
                sync_mv=[min(s['sync_mv'] for s in strong), max(s['sync_mv'] for s in strong)],
                video_mv=[min(s['video_mv'] for s in strong), max(s['video_mv'] for s in strong)])


def accept(result):
    s = result['summary']
    failures = []
    if s['walk_max_codes'] > LIMIT_WALK_CODES:
        failures.append(f"level moved {s['walk_max_codes']} codes within one context (limit {LIMIT_WALK_CODES})")
    if s['cold_max_codes'] > LIMIT_COLD_CODES:
        failures.append(f"level learned weak differs {s['cold_max_codes']} codes from strong (limit {LIMIT_COLD_CODES})")
    if s['cold_learned_from_cn_db'] > LIMIT_COLD_LEARNED_CN:
        failures.append(f"a cold start learns only from C/N {s['cold_learned_from_cn_db']} dB (limit {LIMIT_COLD_LEARNED_CN})")
    if s['clipped_max'] > LIMIT_CLIPPED:
        failures.append(f"{s['clipped_max']} of the output on code 0 or 63 (limit {LIMIT_CLIPPED})")
    if not all(row['steps'][0]['learned'] for row in result['rows']):
        failures.append('a strong carrier did not set the level')
    for k in ('tip', 'blank', 'sync'):
        if s['across_strong_codes'][k] > LIMIT_ACROSS_CODES:
            failures.append(f"{k} differs {s['across_strong_codes'][k]} codes across VTXs/decoders (limit {LIMIT_ACROSS_CODES})")
    return failures


def same(a, b, key=''):
    """The stored result, to a tenth of a code / 1 mV (filter rounding differs between SciPy builds)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k], k) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y, key) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, str) or isinstance(b, str):
        return a == b
    if key in ('windows_used', 'clipped', 'clipped_max', 'clipped_wide_white_cold_max'):
        return abs(a - b) <= (4 if key == 'windows_used' else 0.01)
    return abs(a - b) <= (1.0 if key.endswith('_mv') else 0.11)


if __name__ == '__main__':
    result = run()
    print('summary', json.dumps(result['summary']))
    failures = accept(result)
    for f in failures:
        print('FAIL', f)
    if '--write' in sys.argv:
        OUT.write_text(json.dumps(result, indent=1) + '\n')
        print('wrote', OUT)
    if '--check' in sys.argv:
        if failures:
            sys.exit(1)
        if not same(json.loads(OUT.read_text()), json.loads(json.dumps(result))):
            sys.exit('research/data/output_consistency.json is not current: run with --write')
        print('AUTO OUTPUT consistency: acceptance limits hold, data file current')
