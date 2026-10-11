#!/usr/bin/env python3
"""Information ledger: where does picture information (and where do clicks)
come from in the C5 demod chain? (C5VRX by Twotoz/contributors)

Each stage adds exactly one loss to the previous one, on identical signals:
  A40   analog IQ, ideal discriminator at 40 MS/s (RX5808-style reference)
  A20   analog IQ, span50 on sample A only (20 MS/s)
  Q40c  4-bit lane cells, *saturating* (no fold), discriminator at 40 MS/s
  Q40   4-bit lane cells as captured (sign + 3 bits: large values fold)
  Q20   Q40 on sample A only, span50
  S32   Q20 with the sample angle quantized to 32 sectors (U85 tokens)
  T85   32-state kp 0.85 tracker on S32 tokens, float output
  U85   the flashed LUT (6-bit DAC over -4..+8 MHz)
Reported per stage: correctly displayed lines (min of both goggle models),
luma SINAD, and large errors (|error| > 40 IRE per 1000 active samples,
i.e. clicks). Synthetic engineering measurement only.
"""
import json
import multiprocessing as mp
import os
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
STAGES = tuple(os.environ.get('LEDGER_STAGES', 'A40,A20,Q40c,Q40,Q20,S32,T85,U85').split(','))


def out20(v, n):
    """Duplicate a 20 MS/s stream to the 40 MS/s DAC grid, through the goggle filter."""
    import overlay_fsm as O
    y = np.zeros(n); y[2:2 + 2 * len(v):2] = v; y[3:3 + 2 * len(v):2] = v
    return O.H.B.D.goggle(y)


def wrap(x):
    return (x + np.pi) % (2 * np.pi) - np.pi


def cells_saturating(y, shift=4):
    """Hypothetical lane without fold: clip to the 4-bit range instead of wrapping."""
    c = lambda x: np.clip(np.floor(x), -8, 7) + .5
    return c(y.real) + 1j * c(y.imag)


def cells_folded(raw):
    i = (raw.astype(np.int64) >> 4) & 15; q = raw.astype(np.int64) & 15
    return (np.where(i > 7, i - 16, i) + .5) + 1j * (np.where(q > 7, q - 16, q) + .5)


def stage(name, analog, raw):
    import overlay_fsm as O
    n = len(raw)
    if name == 'A40':
        d = np.angle(analog[1:] * np.conj(analog[:-1])); return O.H.B.D.goggle(np.r_[d[:1], d])
    if name == 'A20':
        a = analog[0::2]; return out20(np.angle(a[1:] * np.conj(a[:-1])), n)
    z = cells_saturating(analog) if name == 'Q40c' else cells_folded(raw)
    if name in ('Q40c', 'Q40'):
        d = np.angle(z[1:] * np.conj(z[:-1])); return O.H.B.D.goggle(np.r_[d[:1], d])
    a = z[0::2]
    if name == 'Q20':
        return out20(np.angle(a[1:] * np.conj(a[:-1])), n)
    T = 32
    obs = (np.floor((np.angle(a) + np.pi) * T / (2 * np.pi)) % T + .5) * 2 * np.pi / T - np.pi
    if name == 'S32':
        return out20(wrap(obs[1:] - obs[:-1]), n)
    if name == 'T85':
        P = 32; ph = 0.; out = np.zeros(len(obs))
        for k in range(len(obs)):
            pq = (np.floor((ph + np.pi) * P / (2 * np.pi)) % P + .5) * 2 * np.pi / P - np.pi
            e = wrap(obs[k] - pq); out[k] = e; ph = wrap(pq + .85 * e)
        return out20(out[1:], n)
    if name in ('F85E', 'F85Eq'):
        # Float upper bound of U85E: exact cell angle (or 32-sector for F85Eq),
        # continuous phase state, origin erasure, output clipped to -4..+8 MHz.
        lo, hi = (2 * np.pi * f * 50e-9 for f in (-4e6, 8e6))
        ang = np.angle(a)
        if name == 'F85Eq':
            ang = (np.floor((ang + np.pi) * 31 / (2 * np.pi)) % 31 + .5) * 2 * np.pi / 31 - np.pi
        er = (abs(a.real) <= .5) & (abs(a.imag) <= .5)
        ph = 0.; out = np.zeros(len(a))
        for k in range(len(a)):
            if er[k]: out[k] = 0.; continue
            e = wrap(ang[k] - ph); out[k] = min(max(e, lo), hi); ph = wrap(ph + .85 * e)
        return out20(out[1:], n)
    if name.startswith('FAB'):
        # Both IQ40 samples: 1st-order loop updated with A then B (gain kp
        # each, 25-ns steps), origin cells erased; output = phase advance over
        # the 50-ns span, clipped to -4..+8 MHz. FAB:kp
        parts = name.split(':'); kp = float(parts[1]); kb = float(parts[2]) if len(parts) > 2 else kp
        lo, hi = (2 * np.pi * f * 50e-9 for f in (-4e6, 8e6))
        zz = z[:2 * (len(z) // 2)].copy()
        if name.startswith('FABs'):  # B only as its sign quadrant (PAIR4411 address bits)
            zz[1::2] = np.sign(zz[1::2].real) * 3 + 1j * np.sign(zz[1::2].imag) * 3
        ang = np.angle(zz); er = (abs(zz.real) <= .5) & (abs(zz.imag) <= .5)
        ph = 0.; out = np.zeros(len(zz) // 2); prev = 0.
        for k in range(len(zz)):
            if not er[k]:
                e = wrap(ang[k] - ph); ph = wrap(ph + (kb if k & 1 else kp) * e)
            if k & 1:
                out[k // 2] = min(max(wrap(ph - prev), lo), hi); prev = ph
        return out20(out[1:], n)
    if name.startswith('F85W'):
        # Amplitude-weighted 1st-order loop: origin cells erased; the second
        # ring (centre radius ~1.6) uses gain w*0.85 and outputs w*e (shrink
        # toward the carrier); outer cells as F85E. F85W:w
        w = float(name.split(':')[1])
        lo, hi = (2 * np.pi * f * 50e-9 for f in (-3.5e6, 7.5e6))
        ang = np.angle(a); r = np.maximum(abs(a.real), abs(a.imag))
        er = r <= .5; ring = (r <= 1.5) & ~er
        ph = 0.; out = np.zeros(len(a))
        for k in range(len(a)):
            if er[k]: continue
            e = wrap(ang[k] - ph); g = w if ring[k] else 1.
            out[k] = min(max(g * e, lo), hi); ph = wrap(ph + .85 * g * e)
        return out20(out[1:], n)
    if name.startswith('F2E'):
        # Float 2nd-order PLL upper bound: phase + frequency state, origin
        # erasure (coast on the frequency), output = frequency + innovation
        # share, clipped to -3.5..+7.5 MHz. F2E:kp:ki:mix
        _, kp, ki, mix = name.split(':'); kp, ki, mix = float(kp), float(ki), float(mix)
        lo, hi = (2 * np.pi * f * 50e-9 for f in (-3.5e6, 7.5e6))
        ang = np.angle(a); er = (abs(a.real) <= .5) & (abs(a.imag) <= .5)
        ph = 0.; fr = 0.; out = np.zeros(len(a))
        for k in range(len(a)):
            pred = ph + fr
            if er[k]: ph = wrap(pred); out[k] = min(max(fr, lo), hi); continue
            e = wrap(ang[k] - pred)
            ph = wrap(pred + kp * e); fr = min(max(fr + ki * e, lo), hi)
            out[k] = min(max(fr + mix * e, lo), hi)
        return out20(out[1:], n)
    if name == 'U85E':
        import untrained_search as U
        m = U.build(dict(layout='4411', token_bits=5, kp=.85, gamma=0., out='linear', sigma=0.,
                         range=(-4., 8.), erase=True, erase_r=.8))
        return O.decode(raw, m)
    if name == 'U85':
        m = json.loads((ROOT / 'tools/range_options.json').read_text())['options'][6]['model']
        return O.decode(raw, m)
    raise ValueError(name)


def job(spec):
    os.environ['CLIFF_BOARD'] = '1'; os.environ['CLIFF_STRESS'] = '1'; os.environ['CLIFF_ANALOG'] = '1'
    import cliff_study as S
    import goggle_lock as G
    seed, cnr, std, dev, rms, pattern = spec
    c = S.make(seed, cnr, std, dev, rms, pattern)
    rows = []
    for name in STAGES:
        y = stage(name, c['analog'], c['raw']); yc = stage(name, c['analog_clean'], c['clean'])
        cal = G.calibrate(yc, c['truth'], c['region']); yi = G.apply(y, cal)
        r = G.measure(y, yc, c)
        m = c['region'][3000:-3000] == 1
        err = (yi - c['truth'])[3000:-3000][m]
        rows.append(dict(stage=name, cnr=cnr, dev=dev, pic=min(r['h_ok'], r['peak_h_ok']), sinad=r['sinad'],
                         clicks=float(1000 * np.mean(abs(err) > 40))))
    return rows


def main():
    sys.path.insert(0, str(ROOT / 'tools'))
    import untrained_search as U
    specs = U.case_specs(int(sys.argv[2]) if len(sys.argv) > 2 else 830000, [4, 6, 8, 10, 14, 20],
                         [.6, .69, 1.0, 1.4])
    with mp.get_context('spawn').Pool(7) as p:
        rows = [r for rr in p.map(job, specs) for r in rr]
    Path(sys.argv[1]).write_text(json.dumps(rows))
    cnrs = sorted(set(r['cnr'] for r in rows))
    print('stage  ' + ' '.join(f'{c:>16.0f}' for c in cnrs) + '   (picture / SINAD / clicks per 1000)')
    for s in STAGES:
        cells = []
        for c in cnrs:
            rs = [r for r in rows if r['stage'] == s and r['cnr'] == c]
            cells.append('%.2f/%4.1f/%4.1f' % tuple(np.mean([r[k] for r in rs]) for k in ('pic', 'sinad', 'clicks')))
        print(f'{s:6s} ' + ' '.join(f'{x:>16s}' for x in cells), flush=True)


if __name__ == '__main__':
    main()
