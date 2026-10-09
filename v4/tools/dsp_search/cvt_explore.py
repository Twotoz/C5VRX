#!/usr/bin/env python3
"""CVT-EDGE explorer: an untrained, analytically derived self-adapting demod.

Operator request 2026-10-09: no trained tables; one demod that adapts
continuously ("like a CVT") and instantly to the IQ. The optimal FM
tracker in noise is a Kalman-type loop whose gain follows the reliability
of each observation. Here every 50-ns span carries:
- angle of sample A (cell centres, the C5VRX Q4/I4 convention);
- reliability from physics, not data: amplitude of A (phase near the
  origin is unreliable) and whether the sign bits of sample B (25 ns later,
  the PAIR4411 address) agree with A's angle (disagreement means noise or a
  click). Weight w = a^2 / (a^2 + s^2) per class, scaled down when B
  disagrees: a per-sample, instantly varying loop gain;
- a soft error limiter (tanh): small errors follow detail, large ones
  (clicks) are damped.
Every candidate is the exact finite-state machine a LUT would run:
P phases x F frequencies x T tokens with P*F*T <= 1024, T = angles x classes.
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


def token_table(A, C, amp_split, b_weight, sigma):
    """Analytic decoder: 10-bit PAIR4411 address -> (angle token, class) and
    the class weights. Address bits: Q_A 0..3, I_A 4..7, Q_B sign 8, I_B sign 9."""
    tok = np.zeros(1024, np.int64); obs = np.zeros(A * C); w = np.zeros(A * C)
    amp_cls = np.zeros(1024, np.int64); ang = np.zeros(1024)
    for a in range(1024):
        qa = a & 15; ia = (a >> 4) & 15
        q = (qa - 16 if qa > 7 else qa) + .5; i = (ia - 16 if ia > 7 else ia) + .5
        th = math.atan2(q, i); r = math.hypot(i, q)
        qb_neg = (a >> 8) & 1; ib_neg = (a >> 9) & 1
        # B consistent: its quadrant centre within 90 degrees of A's angle.
        bc = math.atan2(-1. if qb_neg else 1., -1. if ib_neg else 1.)
        consistent = abs(_wrap(bc - th)) <= math.pi / 2
        sec = int((th + math.pi) * A / (2 * math.pi)) % A
        if C == 1: cls = 0
        elif C == 2: cls = 0 if (r >= amp_split and consistent) else 1
        else: cls = (0 if r >= amp_split else 1) * 2 + (0 if consistent else 1)
        tok[a] = sec * C + cls; ang[a] = th; amp_cls[a] = cls
    for t in range(A * C):
        m = tok == t
        obs[t] = math.atan2(np.sum(np.sin(ang[m])), np.sum(np.cos(ang[m]))) if m.any() else 0.
        cls = t % C
        if C == 1: rr = 4.
        elif C == 2: rr = 5.5 if cls == 0 else 2.
        else: rr = (5.5 if cls < 2 else 2.)
        wc = rr * rr / (rr * rr + sigma * sigma)
        if (C == 2 and cls == 1) or (C == 4 and cls % 2 == 1): wc *= b_weight
        w[t] = wc if C > 1 else 1.
    return tok, obs, w


@njit(cache=True)
def run(raw, tok, obs, w, P, F, kp, ki, soft, fmin, fmax):
    n = len(raw) // 2 - 1; out = np.zeros(n)
    step = (fmax - fmin) / (F - 1); p = 0; f = (F - 1) // 2
    for k in range(n):
        a = np.int64(raw[2 * k]); b = np.int64(raw[2 * k + 1])
        addr = (a & 15) | (((a >> 4) & 15) << 4) | (((b >> 3) & 1) << 8) | (((b >> 7) & 1) << 9)
        t = tok[addr]
        ph = p * 2 * math.pi / P; fr = fmin + f * step; pred = ph + fr
        e = _wrap(obs[t] - pred)
        e = soft * math.tanh(e / soft) * w[t]
        f = min(F - 1, max(0, int(round((fr + ki * e - fmin) / step))))
        p = int(math.floor((pred + kp * e) * P / (2 * math.pi) + .5)) % P
        out[k] = fmin + f * step
    return out


def decode(c, cfg, table):
    import engine as S
    dev, centre = c['fit']['fit_deviation'], c['fit']['fit_centre_hz']
    lo = centre + dev * (-4.2e6); hi = centre + dev * (4.2e6)
    f = run(c['raw'], *table, cfg['P'], cfg['F'], cfg['kp'], cfg['ki'], cfg['soft'],
            2 * math.pi * lo / 20e6, 2 * math.pi * hi / 20e6)
    cn = 2 * math.pi * 1e6 / 20e6; cm = 2 * math.pi * centre / 20e6; o = cn + (f - cm) / dev
    code = np.rint(np.clip((o * 128 / np.pi - S.H.B.P.OFFSET) * S.H.B.P.SCALE, 0, 63)).astype(int)
    y = np.zeros(len(c['raw'])); y[2:2 + 2 * len(code)] = np.repeat(S.H.B.DAC_VOLTS[code], 2)
    return S.H.B.D.goggle(y)


def configs():
    for (P, F, A, C) in ((4, 32, 8, 1), (4, 32, 4, 2), (4, 16, 8, 2), (4, 16, 4, 4), (8, 16, 4, 2), (4, 8, 8, 4)):
        if P * F * A * C > 1024: continue
        for kp, ki, soft in itertools.product((.7, 1.), (.1, .2), (1.2, 3.1)):
            for amp_split, b_weight, sigma in (((3., .4, 2.), (3., .2, 3.), (4.5, .4, 2.)) if C > 1 else ((0, 1, 1),)):
                yield dict(P=P, F=F, A=A, C=C, kp=kp, ki=ki, soft=soft, amp_split=amp_split, b_weight=b_weight, sigma=sigma)


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
        table = token_table(cfg['A'], cfg['C'], cfg['amp_split'], cfg['b_weight'], cfg['sigma'])
        r = {}
        for c in cases:
            r.setdefault(c['cnr'], []).append(TI.score(decode(c, cfg, table), c))
        rows.append(dict(cfg=cfg, weak_missed=int(sum(v[0] for k in (2, 4, 6) for v in r[k])),
                         weak_false=float(np.mean([v[2] for k in (2, 4, 6) for v in r[k]])),
                         mid_sinad=float(np.mean([v[1] for v in r[8]])),
                         strong_sinad=float(np.mean([v[1] for k in (13, 20, 30) for v in r[k]]))))
    for x in rows: x['objective'] = x['strong_sinad'] + .5 * x['mid_sinad'] - .5 * x['weak_missed'] - 10 * x['weak_false']
    rows.sort(key=lambda x: -x['objective'])
    (a.output / 'cvt.json').write_text(json.dumps(rows, indent=1))
    for x in rows[:12]:
        c = x['cfg']
        print(f"P{c['P']}xF{c['F']} A{c['A']}xC{c['C']} kp{c['kp']} ki{c['ki']} soft{c['soft']} split{c['amp_split']} bw{c['b_weight']} "
              f"s{c['sigma']}: weak missed {x['weak_missed']:3d} false {x['weak_false']:.2f} | SINAD 8dB {x['mid_sinad']:4.1f} "
              f"strong {x['strong_sinad']:4.1f}", flush=True)
    base = max((x for x in rows if x['cfg']['C'] == 1), key=lambda x: x['objective'])
    print('best without reliability classes:', base['cfg'], base['weak_missed'], round(base['weak_false'], 2), round(base['strong_sinad'], 1))


if __name__ == '__main__':
    main()
