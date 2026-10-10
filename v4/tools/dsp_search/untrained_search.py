#!/usr/bin/env python3
"""Search for the best *untrained* demod (C5VRX by Twotoz/contributors).

Every candidate is a closed-form model with a handful of physical parameters;
no LUT entry is fitted to data. The search only chooses among formulas, and
the winners are re-scored on held-out seeds. All candidates compile to the
existing PAIR-style shared-word program: a 10-bit address of chosen IQ40 bits
-> token (high bits), and (state, token) -> next state | 6-bit DAC.

Parameters
- layout: kept raw bits (sample A I/Q, sample B I/Q), e.g. 4411, 3322;
- token_bits b: tokens = 2**b angle bins, states = 2**(10-b) phase bins;
- gamma: weight (cells) of sample B's direction added to sample A's vector;
- kp: phase update p' = p + kp*wrap(obs - p) (kp = 1: state = last obs);
- out: 'linear' (wrapped phase advance) or 'mmse' (posterior mean of the
  advance under wrapped-Gaussian noise sigma and a uniform prior on the
  plausible frequency range - a physical soft clamp);
- range: frequency range mapped onto the 64 DAC codes ('nominal' = the PAIR
  transfer used by UP1, or explicit MHz limits).

Scoring uses goggle_lock.py over full fields, both goggle AGC models, board
conditions and flight stress (cliff_study.make). Objective: picture first -
mean over C/N buckets of min(ideal, peak) correctly displayed lines - then
mean luma SINAD.
"""
import argparse
import itertools
import json
import math
import os
import sys
import time
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def params_grid():
    out = []
    ranges = ['nominal', (-5.0, 9.5), (-4.0, 8.0)]
    outs = [('linear', 0.)] + [('mmse', s) for s in (.35, .7, 1.1)]
    for layout, b, kp, gamma, (mode, sigma), rng in itertools.product(
            ('4411', '3322'), (4, 5, 6), (1.0, .8), (0., 1., 2.), outs, ranges):
        if layout == '3322' and gamma == 0.: continue  # B bits would be unused
        out.append(dict(layout=layout, token_bits=b, kp=kp, gamma=gamma, out=mode, sigma=sigma,
                        range=rng))
    return out


def key(p):
    r = p['range'] if p['range'] == 'nominal' else '%g..%g' % tuple(p['range'])
    return f"L{p['layout']}-b{p['token_bits']}-kp{p['kp']}-g{p['gamma']}-{p['out']}{p['sigma'] or ''}-{r}"


def _nibble(word, positions, kept):
    """Signed 4-bit cell value from the kept top bits; dropped bits at midpoint."""
    v = 0
    for bit in range(4):
        if bit >= 4 - kept: v |= ((word >> positions[bit]) & 1) << bit
    if kept < 4: v |= 1 << (3 - kept)  # midpoint of the dropped range
    v = v - 16 if v > 7 else v
    return v + (.5 if kept == 4 else 0.)


def build(p):
    import compile_overlay as C
    import edge_fsm as F
    B = F.B
    b = p['token_bits']; T = 1 << b; P = 1 << (10 - b)
    bits = C.pair_bits(p['layout'])
    kI, kQ, kIb, kQb = map(int, p['layout'])
    # Address bit j holds raw bit bits[j]; rebuild the raw 16-bit word per address.
    ang = np.zeros(1024)
    for a in range(1024):
        word = 0
        for j in range(10): word |= ((a >> j) & 1) << bits[j]
        za = _nibble(word, (4, 5, 6, 7), kI) + 1j * _nibble(word, (0, 1, 2, 3), kQ)
        zb = _nibble(word, (12, 13, 14, 15), kIb) + 1j * _nibble(word, (8, 9, 10, 11), kQb)
        z = za + (p['gamma'] * zb / abs(zb) if abs(zb) > 0 else 0)
        ang[a] = math.atan2(z.imag, z.real)
    tok = np.floor((ang + np.pi) * T / (2 * np.pi)).astype(int) % T
    obs = (np.arange(T) + .5) * 2 * np.pi / T - np.pi
    ph = (np.arange(P) + .5) * 2 * np.pi / P - np.pi
    # Output transfer: phase advance per 50 ns (rad) -> DAC code.
    if p['range'] == 'nominal':
        def code(theta):
            return np.rint(np.clip((theta * 128 / np.pi - B.P.OFFSET) * B.P.SCALE, 0, 63))
        lo_t, hi_t = -np.pi, np.pi
    else:
        lo_t, hi_t = (2 * np.pi * f * 1e6 * 50e-9 for f in p['range'])
        def code(theta):
            return np.rint(np.clip((theta - lo_t) / (hi_t - lo_t) * 63, 0, 63))
    grid = np.linspace(lo_t, hi_t, 241)
    lut = np.zeros(1024, np.int64)
    for s in range(P):
        for t in range(T):
            e = (obs[t] - ph[s] + np.pi) % (2 * np.pi) - np.pi
            nxt = int(np.floor((ph[s] + p['kp'] * e + np.pi) * P / (2 * np.pi))) % P
            if p['out'] == 'mmse':
                d = (e - grid + np.pi) % (2 * np.pi) - np.pi
                w = np.exp(-.5 * (d / p['sigma']) ** 2)
                theta = float(np.sum(w * grid) / np.sum(w))
            else:
                theta = e
            lut[s * T + t] = int(code(theta)) | (nxt << 6)
    lut |= tok.astype(np.int64) << (16 - b)
    m = dict(name='U-' + key(p), params=dict(token_bits=b, phases=P, confidence_groups=1,
                                             pair_layout=p['layout']), lut=[int(v) for v in lut])
    C.build(m)  # proves the schedule compiles
    return m


SPECS = []


def case_specs(seed, cnrs, devs):
    out = []
    for i, (cnr, dev) in enumerate(itertools.product(cnrs, devs)):
        std = ('PAL', 'NTSC')[i % 2]
        pattern = ('texture', 'bars', 'zoneplate', 'checker')[i % 4]
        rms = 3.5 + 1.5 * ((i * 7919) % 100) / 100
        out.append((seed + 101 * i, float(cnr), std, float(dev), rms, pattern))
    return out


def shard_main(a):
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
    os.environ['CLIFF_BOARD'] = '1'; os.environ['CLIFF_STRESS'] = '1'
    sys.path.insert(0, str(ROOT / 'tools'))
    import cliff_study as S
    import goggle_lock as G
    import overlay_fsm as O
    specs = json.loads(Path(a.specs).read_text())
    cands = json.loads(Path(a.candidates).read_text())
    mine = specs[a.shard::a.shards]
    cases = []
    for sp in mine:
        c = S.make(*sp)
        cases.append(dict(spec=sp, raw=c['raw'], clean=c['clean'], truth=c['truth'].astype(np.float32),
                          region=c['region'], standard=c['standard'], fit=c['fit']))
    done = {}
    out = Path(a.out)
    if out.exists(): done = json.loads(out.read_text())
    t0 = time.time()
    for n, p in cands.items():
        if n in done: continue
        if p.get('control'):
            maker = S.lookup(S.demods(), p['control'])
        else:
            m = build(p); maker = lambda c, m=m: ('lut', m)
        rows = []
        for c in cases:
            kind, m = maker(c)
            y = S.run(kind, m, c['raw']); yc = S.run(kind, m, c['clean'])
            r = G.measure(y, yc, dict(c, truth=c['truth'].astype(float)))
            rows.append(dict(spec=c['spec'], h=r['h_ok'], hp=r['peak_h_ok'], sinad=r['sinad'],
                             false=r['false_per_line']))
        done[n] = rows
        out.write_text(json.dumps(done))
        print(f'shard {a.shard} {len(done)}/{len(cands)} {time.time() - t0:.0f}s {n}', flush=True)


def summarize(results, cands):
    table = {}
    for n in cands:
        rows = [r for part in results for r in part.get(n, [])]
        if not rows: continue
        cnrs = sorted(set(r['spec'][1] for r in rows))
        pic = [np.mean([min(r['h'], r['hp']) for r in rows if r['spec'][1] == c]) for c in cnrs]
        sin = [np.mean([r['sinad'] for r in rows if r['spec'][1] == c]) for c in cnrs]
        worst = min(min(r['h'], r['hp']) for r in rows if r['spec'][1] >= 6)
        table[n] = dict(picture=float(np.mean(pic)), worst=float(worst), sinad=float(np.mean(sin)),
                        per_cnr={str(c): [round(float(x), 3), round(float(y), 2)] for c, x, y in zip(cnrs, pic, sin)})
    return table


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('shard'); s.add_argument('--specs'); s.add_argument('--candidates')
    s.add_argument('--shard', type=int); s.add_argument('--shards', type=int); s.add_argument('--out')
    g = sub.add_parser('grid'); g.add_argument('--output', type=Path, required=True)
    g.add_argument('--seed', type=int, default=700000); g.add_argument('--cnr', default='6,10,20')
    g.add_argument('--dev', default='0.69,1.0,1.4'); g.add_argument('--top', default='')
    g.add_argument('--controls', default='HC50,RANGE32,PAIR_AF,UP1')
    r = sub.add_parser('report'); r.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    if a.cmd == 'shard': return shard_main(a)
    if a.cmd == 'grid':
        a.output.mkdir(parents=True, exist_ok=True)
        specs = case_specs(a.seed, [float(x) for x in a.cnr.split(',')], [float(x) for x in a.dev.split(',')])
        (a.output / 'specs.json').write_text(json.dumps(specs))
        if a.top:
            prev = json.loads(Path(a.top).read_text())
            cands = {n: prev['candidates'][n] for n in prev['top']}
        else:
            cands = {key(p): p for p in params_grid()}
        for c in a.controls.split(','):
            if c: cands['CTRL-' + c] = dict(control=c)
        (a.output / 'candidates.json').write_text(json.dumps(cands))
        print(len(specs), 'cases', len(cands), 'candidates')
        return
    if a.cmd == 'report':
        cands = json.loads((a.output / 'candidates.json').read_text())
        parts = [json.loads(f.read_text()) for f in sorted(a.output.glob('shard*.json'))]
        t = summarize(parts, cands)
        ranked = sorted(t, key=lambda n: (-round(t[n]['picture'], 3), -t[n]['sinad']))
        (a.output / 'summary.json').write_text(json.dumps(dict(table=t, ranked=ranked, candidates=cands,
                                                              top=[n for n in ranked if not n.startswith('CTRL')][:12]), indent=1))
        for n in ranked[:25] + [n for n in ranked if n.startswith('CTRL') and n not in ranked[:25]]:
            x = t[n]
            print(f"{n:48s} picture {x['picture']:.3f} worst {x['worst']:.2f} sinad {x['sinad']:5.2f} | " +
                  ' '.join(f"{c}:{v[0]:.2f}/{v[1]:.1f}" for c, v in x['per_cnr'].items()))


if __name__ == '__main__':
    main()
