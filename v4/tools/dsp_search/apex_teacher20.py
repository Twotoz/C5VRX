#!/usr/bin/env python3
"""APEX 2.0 priority C1: honest C/N-blind, output-matched causal teacher (C5VRX).

PR #190 review (Twotoz, 2026-10-09): the apex_tiers teacher used every
40 MS/s sample and picked its gains with the test C/N. This teacher matches
the C5 output: one decision per 50 ns from sample A only (the sample a LUT
address holds in full), 6-bit DAC, the goggle filter, and a loop-gain
schedule chosen causally from the previous 4096-sample window's envelope
C/N (fusion_demod.h estimator). Gains are tuned on training seeds only.
It splits the teacher advantage into 40-MHz information, 20M-output loss
and finite-state/LUT compression loss.
"""
import argparse
import itertools
import json
import math
from pathlib import Path
import numpy as np
from numba import njit


@njit(cache=True)
def _wrap(x):
    return (x + math.pi) % (2 * math.pi) - math.pi


@njit(cache=True)
def trace20(raw, kp, ki, conf2, limit, mix, leak, centre, out_clip=9.):
    """One decision per 50 ns from sample A; kp/ki per decision (schedule)."""
    n = len(raw) // 2; out = np.zeros(n); phase = 0.; freq = centre
    for k in range(n):
        b = np.int64(raw[2 * k]); i = b >> 4; q = b & 15
        i = (i - 16 if i > 7 else i) + .5; q = (q - 16 if q > 7 else q) + .5
        angle = math.atan2(q, i)
        pred = _wrap(phase + freq); err = _wrap(angle - pred)
        err = min(limit, max(-limit, err))
        pw = i * i + q * q; c = pw / (pw + conf2)
        freq = (1 - leak) * freq + leak * centre + ki[k] * c * err
        freq = min(math.pi * .8, max(-math.pi * .8, freq))
        phase = _wrap(pred + kp[k] * c * err)
        # Bounded output innovation: small phase errors carry detail, large
        # ones are FM clicks (real-IQ veto, PR #190 review 2026-10-09).
        out[k] = freq + max(-out_clip, min(out_clip, mix * kp[k] * c * err))
    return out


def envelope_cnr_x10(raw):
    i = ((raw.astype(np.int16) & 0xf0) >> 4); i = np.where(i > 7, i - 16, i) * 2 + 1.
    q = (raw.astype(np.int16) & 0xf); q = np.where(q > 7, q - 16, q) * 2 + 1.
    A = np.c_[2 * i, 2 * q, np.ones_like(i)]
    (a, b, _), *_ = np.linalg.lstsq(A, i * i + q * q, rcond=None)
    r2 = (i - a) ** 2 + (q - b) ** 2; R = r2.mean() ** 2 / max(r2.var(), 1e-9)
    if R <= 1: return -99
    rho = (R - 1) + math.sqrt(R * (R - 1)); return int(round(100 * math.log10(rho)))


def schedule(raw, weak, strong, threshold_x10, window=4096):
    """Causal gains: window w uses the C/N of window w-1 (first: weak)."""
    n = len(raw) // 2; kp = np.empty(n); ki = np.empty(n); prev = -99
    for w in range(0, len(raw), window):
        g = strong if prev >= threshold_x10 else weak
        a, b = w // 2, min(n, (w + window) // 2); kp[a:b] = g[0]; ki[a:b] = g[1]
        prev = envelope_cnr_x10(raw[w:w + window])
    return kp, ki


def decode(raw, p, dev, centre_hz, weak, strong, threshold_x10=90):
    import engine as S
    kp, ki = schedule(raw, weak, strong, threshold_x10)
    freq = trace20(raw, kp, ki, p['conf'] ** 2, p['limit'], p['mix'], p['leak'], 2 * math.pi * 1e6 / 20e6)
    cn = 2 * math.pi * 1e6 / 20e6; cm = 2 * math.pi * centre_hz / 20e6
    out = cn + (freq - cm) / dev
    code = np.rint(np.clip((out * 128 / np.pi - S.H.B.P.OFFSET) * S.H.B.P.SCALE, 0, 63)).astype(int)
    # Same latency as the LUT executor (output of span k at samples 2k+2..3).
    y = np.zeros(len(raw)); y[2:] = np.repeat(S.H.B.DAC_VOLTS[code], 2)[:len(raw) - 2]
    return S.H.B.D.goggle(y)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--train-seeds', type=int, nargs='+', default=[451000])
    ap.add_argument('--test-seeds', type=int, nargs='+', default=[461000, 462000])
    a = ap.parse_args()
    if a.output.exists(): ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    import ladder_edge as LE
    import apex_tiers as T
    root = Path(__file__).resolve().parents[2]
    hyp = json.loads((root / 'tools/detector_study/models/theory_hypotheses.json').read_text())
    p0 = np.array(next(m for m in hyp['winners'] if m['family'] == 'pll')['params'], float)
    p = dict(conf=p0[2], limit=p0[3], mix=p0[4], leak=p0[5])
    train = [c for s in a.train_seeds for c in LE.cases(s, per=4, afc=True)]
    fit = lambda c: (c['fit']['fit_deviation'], c['fit']['fit_centre_hz'])
    # Tune the two gain sets on training seeds only: strong on C/N >= 13,
    # weak on C/N <= 8 (fixed gains, i.e. a constant schedule).
    grid = [(p0[0] * s, p0[1] * t) for s, t in itertools.product((.5, 1, 1.5, 2, 3), (.5, 1, 2, 4))]
    def loss(g, sel):
        tot = 0.
        for c in train:
            if not sel(c['cnr']): continue
            m, s, f = T.score(decode(c['raw'], p, *fit(c), g, g, 999), c)
            tot += 4 * m + 20 * f - s
        return tot
    strong = min(grid, key=lambda g: loss(g, lambda x: x >= 13))
    weak = min(grid, key=lambda g: loss(g, lambda x: x <= 8))
    test = [c for s in a.test_seeds for c in LE.cases(s, per=6, afc=True)]
    rows = {}
    for c in test:
        rows.setdefault(c['cnr'], []).append(T.score(decode(c['raw'], p, *fit(c), weak, strong), c))
    table = {str(k): dict(missed=int(sum(v[0] for v in r)), sinad=float(np.mean([v[1] for v in r])),
                          false=float(np.mean([v[2] for v in r]))) for k, r in rows.items()}
    (a.output / 'teacher20.json').write_text(json.dumps(dict(weak=weak, strong=strong, train_seeds=a.train_seeds,
                                                             test_seeds=a.test_seeds, table=table), indent=1))
    print('gains weak', np.round(weak, 4), 'strong', np.round(strong, 4))
    for k in sorted(rows):
        t = table[str(k)]; print(f"C/N {k:2d}: teacher20 C/N-blind missed {t['missed']:3d} SINAD {t['sinad']:4.1f} false {t['false']:.2f}")


if __name__ == '__main__':
    main()
