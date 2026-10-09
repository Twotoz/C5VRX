#!/usr/bin/env python3
"""APEX 2.0 architecture explorer (C5VRX by Twotoz/contributors).

Deep research before training (operator 2026-10-09; PR #190 review):
which use of the 10 LUT address bits comes closest to the click-safe,
C/N-blind teacher (second-order loop, frequency output, apex_teacher20)?
Each candidate is simulated exactly as the finite-state machine a LUT
would execute: phase index (P levels), frequency index (F levels, AutoFit
grid), observation token (T levels) with P*F*T <= 1024 transition words,
next state = rounded teacher update, output = frequency level.

Observation encoders (decoder lookup, 10 address bits):
- 'abs':   token = absolute angle sector of sample A (EDGE-like);
- 'derot': the decoder address also holds the top d phase bits of the
           state (8 raw bits of A + d <= 2 state bits); the token is the
           angle relative to that sector, finely binned near zero
           (companding gamma). Lookup 2 knows the full phase.
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
def run(raw, P, F, T, d, gamma, kp, ki, limit, fmin, fmax):
    n = len(raw) // 2; out = np.zeros(n)
    step = (fmax - fmin) / (F - 1)
    p = 0; f = (F - 1) // 2
    sector = 2 * math.pi / (1 << d) if d else 2 * math.pi
    for k in range(n):
        b = np.int64(raw[2 * k]); i = b >> 4; q = b & 15
        i = (i - 16 if i > 7 else i) + .5; q = (q - 16 if q > 7 else q) + .5
        angle = math.atan2(q, i)
        ph = p * 2 * math.pi / P; fr = fmin + f * step
        if d:
            # Decoder sees the top d phase bits: relative angle to the sector centre.
            top = (p * (1 << d)) // P
            centre = (top + .5) * sector - math.pi + math.pi / P * 0
            rel = _wrap(angle - centre)
            u = rel / math.pi                      # -1..1
            v = math.copysign(abs(u) ** (1. / gamma), u)
            t = min(T - 1, int((v + 1) * T / 2))
            vc = (t + .5) * 2 / T - 1
            obs = centre + math.copysign(abs(vc) ** gamma, vc) * math.pi
        else:
            t = int((angle + math.pi) * T / (2 * math.pi)) % T
            obs = (t + .5) * 2 * math.pi / T - math.pi
        pred = ph + fr
        e = _wrap(obs - pred)
        if e > limit: e = limit
        if e < -limit: e = -limit
        fn = int(round((fr + ki * e - fmin) / step))
        f = min(F - 1, max(0, fn))
        p = int(math.floor((pred + kp * e) * P / (2 * math.pi) + .5)) % P
        out[k] = fmin + f * step
    return out


def decode(c, cfg):
    import engine as S
    dev, centre = c['fit']['fit_deviation'], c['fit']['fit_centre_hz']
    lo = centre + dev * (-3.2e6 - 1e6); hi = centre + dev * (5.2e6 - 1e6)
    fmin = 2 * math.pi * lo / 20e6; fmax = 2 * math.pi * hi / 20e6
    f = run(c['raw'], cfg['P'], cfg['F'], cfg['T'], cfg['d'], cfg['gamma'], cfg['kp'], cfg['ki'],
            cfg['limit'], fmin, fmax)
    cn = 2 * math.pi * 1e6 / 20e6; cm = 2 * math.pi * centre / 20e6
    o = cn + (f - cm) / dev
    code = np.rint(np.clip((o * 128 / np.pi - S.H.B.P.OFFSET) * S.H.B.P.SCALE, 0, 63)).astype(int)
    # Same latency as the LUT executor (output of span k at samples 2k+2..3).
    y = np.zeros(len(c['raw'])); y[2:] = np.repeat(S.H.B.DAC_VOLTS[code], 2)[:len(c['raw']) - 2]
    return S.H.B.D.goggle(y)


def configs():
    for (P, F, T) in ((4, 32, 8), (8, 16, 8), (16, 8, 8), (8, 32, 4), (16, 16, 4), (32, 8, 4), (4, 16, 16), (8, 8, 16)):
        for d in (0, 1, 2):
            if d and (1 << d) > P: continue
            for gamma in ((1.,) if not d else (1., 1.6, 2.4)):
                for kp, ki in itertools.product((.5, .7, 1.), (.1, .2)):
                    yield dict(P=P, F=F, T=T, d=d, gamma=gamma, kp=kp, ki=ki, limit=math.pi)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--seed', type=int, default=451000)
    a = ap.parse_args()
    if a.output.exists(): ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    import ladder_edge as LE
    import apex_tiers as TI
    cases = [c for c in LE.cases(a.seed, per=4, afc=True) if c['cnr'] in (2, 4, 6, 8, 13, 20, 30)]
    rows = []
    for cfg in configs():
        r = {}
        for c in cases:
            r.setdefault(c['cnr'], []).append(TI.score(decode(c, cfg), c))
        weak_miss = sum(v[0] for k in (2, 4, 6) for v in r[k])
        weak_false = float(np.mean([v[2] for k in (2, 4, 6) for v in r[k]]))
        strong = float(np.mean([v[1] for k in (13, 20, 30) for v in r[k]]))
        mid = float(np.mean([v[1] for k in (8,) for v in r[k]]))
        rows.append(dict(cfg=cfg, weak_missed=int(weak_miss), weak_false=weak_false, mid_sinad=mid, strong_sinad=strong,
                         objective=strong + .5 * mid - .5 * weak_miss - 10 * weak_false))
    rows.sort(key=lambda x: -x['objective'])
    (a.output / 'explore.json').write_text(json.dumps(rows, indent=1))
    for x in rows[:15]:
        c = x['cfg']
        print(f"P{c['P']}xF{c['F']}xT{c['T']} d{c['d']} g{c['gamma']} kp{c['kp']} ki{c['ki']}: weak missed {x['weak_missed']:3d} "
              f"false {x['weak_false']:.2f} | SINAD 8dB {x['mid_sinad']:4.1f} strong {x['strong_sinad']:4.1f}", flush=True)
    best_abs = next(x for x in rows if x['cfg']['d'] == 0)
    print('best absolute-token:', best_abs['cfg'], best_abs['weak_missed'], round(best_abs['strong_sinad'], 1))


if __name__ == '__main__':
    main()
