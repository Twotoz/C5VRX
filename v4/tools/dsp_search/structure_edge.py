#!/usr/bin/env python3
"""Structural range-edge demod grid (C5VRX by Twotoz/contributors).

Instead of fine-tuning many parameters of one formula, enumerate thousands of
structurally different second-order edge demods (detector, loop leak, output
reconstruction, frequency grid, reliability weighting, decoder, allocation)
with only kp/ki/hold on a coarse three-value grid. Stage 1 scores every model
on one edge screen; stage 2 re-scores the best decile on three more fresh
screens and ranks by the mean, which limits fitting to one noise realization.
Real-board checks and confirmation are separate (select_edge.py).
"""
import argparse
import hashlib
import itertools
import json
import multiprocessing as mp
import os
from pathlib import Path
import time
import numpy as np

ALLOCATIONS = [(8, 16, 8), (16, 8, 8), (8, 8, 16), (4, 32, 8), (16, 16, 4), (8, 32, 4), (16, 4, 16), (32, 4, 8)]
DETECTORS = ('clip', 'tanh', 'sine', 'softhold')
OUTPUTS = ('freq', 'advance', 'avg')
GRIDS = ('uniform', 'companded')
LEAKS = (0., .03)
DECODERS = [None, ('4411', 'weak', 0), ('4411', 'uniform', 0)]
KP, KI, HOLD = (.4, .7, 1.), (.1, .2, .35), (1., 1.8, 3.2)
STATE = {}


def structures():
    for d, dec in enumerate(DECODERS):
        for rel in ((False, True) if dec else (False,)):
            for alloc, det, out, grid, leak in itertools.product(ALLOCATIONS, DETECTORS, OUTPUTS, GRIDS, LEAKS):
                yield dict(decoder=d, reliability=rel, alloc=alloc, detector=det, output=out, grid=grid, leak=leak)


def params(st, kp, ki, hold, bank):
    P, F, T = st['alloc']
    p = dict(token_bits=int(T).bit_length() - 1, phases=P, frequencies=F, kp=kp, ki=ki, hold=hold,
             limit=np.pi, low_hz=-3.2e6, high_hz=5.2e6, rotation=0., mix=0., detector=st['detector'],
             output=st['output'], grid=st['grid'], leak=st['leak'], reliability=st['reliability'], autofit=True)
    if DECODERS[st['decoder']]:
        p.update(pair_layout='4411', observation_vectors=bank[st['decoder']])
    return p


def setup(seeds, bank):
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
    import search_edge as E
    STATE.update(E=E, bank=bank, screens=[E.screen(s) for s in seeds])


def stage(item):
    import edge_fsm as F
    idx, st, kp, ki, hold, screens = item
    try:
        m = F.synthesize(params(st, kp, ki, hold, STATE['bank']))
    except ValueError:
        return idx, None
    rs = [STATE['E'].score(m, STATE['screens'][k]) for k in screens]
    if any(r is None for r in rs):
        return idx, None
    return idx, [r['score'] for r in rs]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True); ap.add_argument('--seed-base', type=int, required=True)
    ap.add_argument('--workers', type=int, default=7); ap.add_argument('--decile', type=float, default=.1)
    a = ap.parse_args()
    if a.output.exists(): ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    import engine as S
    import pair_decoder as P
    import lane_profile as L
    bank = [None] + [P.learn(l, a.seed_base + 601 + s, m)[0] for l, m, s in DECODERS[1:]]
    sts = list(structures())
    grid = [(i, st, kp, ki, hold) for i, (st, kp, ki, hold) in
            enumerate((st, kp, ki, hold) for st in sts for kp in KP for ki in KI for hold in HOLD)]
    seeds = [a.seed_base + 201 + 10 * k for k in range(4)]
    S.save(a.output / 'protocol.json', dict(profile='edge_structure', config_seed=a.seed_base + 101,
           structures=len(sts), models=len(grid), screen_seeds=seeds, lane_profile=L.record(),
           blocks=dict(allocations=ALLOCATIONS, detectors=DETECTORS, outputs=OUTPUTS, grids=GRIDS, leaks=LEAKS,
                       decoders=DECODERS, kp=KP, ki=KI, hold=HOLD),
           rule='stage1 one screen; best decile re-scored on three more; rank by mean of four',
           source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}))
    start = time.monotonic()
    with mp.get_context('spawn').Pool(a.workers, initializer=setup, initargs=(seeds, bank)) as pool:
        first = dict(pool.imap_unordered(stage, [(i, st, kp, ki, h, (0,)) for i, st, kp, ki, h in grid], chunksize=16))
        ok = sorted((i for i, v in first.items() if v), key=lambda i: -first[i][0])
        keep = ok[:max(50, int(len(ok) * a.decile))]
        second = dict(pool.imap_unordered(stage, [(i, *grid[i][1:], (1, 2, 3)) for i in keep], chunksize=4))
    rows = []
    for i in keep:
        if second.get(i):
            sc = [first[i][0]] + second[i]; _, st, kp, ki, h = grid[i]
            rows.append(dict(index=i, structure=st, kp=kp, ki=ki, hold=h, scores=sc,
                             mean=float(np.mean(sc)), worst=float(np.min(sc))))
    rows.sort(key=lambda r: -r['mean'])
    S.save(a.output / 'ranking.json', rows[:500])
    finalists = [dict(id=f"S{r['index']}", params=params(r['structure'], r['kp'], r['ki'], r['hold'], bank),
                      metrics=r) for r in rows[:40]]
    S.save(a.output / 'frozen.json', dict(finalists=finalists, no_reselection=True))
    summary = dict(structures=len(sts), models=len(grid), stage1_pass=len(ok), stage2=len(keep),
                   four_screen_pass=len(rows), best=rows[0] if rows else None, elapsed_s=time.monotonic() - start)
    S.save(a.output / 'summary.json', summary); print(json.dumps(summary, default=float), flush=True)


if __name__ == '__main__':
    main()
