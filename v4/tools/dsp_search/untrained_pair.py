#!/usr/bin/env python3
"""Untrained PAIR-structured tracker vs PAIR (C5VRX by Twotoz/contributors).

Operator 2026-10-09: prefer an untrained model. The earlier untrained
sharp test used EDGE-shaped allocations; this keeps PAIR's own structure
(32 phase states x 32 tokens, PAIR4411 address, same program) but every
word is analytic: token = 32-sector angle of sample A, phase update
p' = p + kp * wrap(obs - p), output = the phase advance wrap(obs - p) per
50 ns through the nominal FM transfer with the AutoFit remap. Scored with
latency-aligned scoring on held-out randomized cases (deviation 0.6-1.4)
and the real-IQ click check, next to PAIR+AutoFit.
"""
import json
import math
from pathlib import Path
import numpy as np


def analytic_pair(kp, dev, centre, smooth=0.):
    import edge_fsm as F
    import compile_overlay as C
    B = F.B
    P = T = 32
    ang = np.zeros(1024)
    for a in range(1024):
        ia = a & 15; qa = (a >> 4) & 15       # pair_bits('4411'): I_A on 0..3, Q_A on 4..7
        ang[a] = math.atan2((qa - 16 if qa > 7 else qa) + .5, (ia - 16 if ia > 7 else ia) + .5)
    tok = (np.floor((ang + np.pi) * T / (2 * np.pi)).astype(int)) % T
    obs = (np.arange(T) + .5) * 2 * np.pi / T - np.pi
    cn = 2 * np.pi * 1e6 / 20e6; cm = 2 * np.pi * centre / 20e6
    lut = np.zeros(1024, np.int64)
    for s in range(P):
        ph = s * 2 * np.pi / P - np.pi + np.pi / P
        for t in range(T):
            e = (obs[t] - ph + np.pi) % (2 * np.pi) - np.pi
            nxt = int(np.floor((ph + kp * e + np.pi) * P / (2 * np.pi))) % P
            f = e * (1 - smooth) + smooth * kp * e
            out = cn + (f - cm) / dev
            code = int(np.rint(np.clip((out * 128 / np.pi - B.P.OFFSET) * B.P.SCALE, 0, 63)))
            lut[s * T + t] = code | (nxt << 6)
    lut |= tok.astype(np.int64) << 11
    params = dict(token_bits=5, phases=P, confidence_groups=1, pair_layout='4411')
    m = dict(name='UPAIR', params=params, lut=[int(v) for v in lut])
    C.build(m)
    return m


def main():
    import ladder_edge as LE
    import apex_dac as AD
    import select_edge as SE
    from pair_autofit import remap
    root = Path(__file__).resolve().parents[2]
    pair = next(o for o in json.loads((root / 'tools/range_options.json').read_text())['options']
                if o['label'] == 'PAIR RANGE LAB')['model']
    test = [c for s in (481000, 482000) for c in LE.cases(s, per=4, afc=True) if c['cnr'] >= 6]
    ctr = {'PAIR+AF': lambda c: remap(pair, c['fit']['fit_deviation'], c['fit']['fit_centre_hz'])}
    for kp in (1., .8, .6):
        ctr[f'UPAIR kp{kp}'] = (lambda kp: lambda c: analytic_pair(kp, c['fit']['fit_deviation'], c['fit']['fit_centre_hz']))(kp)
    r = AD.evaluate(test, ctr)
    strong = [np.fromfile(f, np.uint8) for f in sorted((Path.home() / 'c5vrx4-static-224006').glob('*.bin'))]
    weak = [np.fromfile(f, np.uint8) for f in sorted((Path.home() / 'c5vrx4-static-224103').glob('*.bin'))]
    fitc = {'fit': dict(fit_deviation=.69, fit_centre_hz=-436e3 + .69 * 1436e3)}
    for n, mk in ctr.items():
        ck = SE.real_click_increase(mk(fitc), strong, weak)
        print(n.ljust(13), ' | '.join('C/N%d %d/%.1f' % (k, sum(v[0] for v in r[(k, n)]), np.mean([v[1] for v in r[(k, n)]]))
                                      for k in (6, 8, 10, 13, 16, 20, 30)), '| clicks %.2f' % ck, flush=True)


if __name__ == '__main__':
    main()
