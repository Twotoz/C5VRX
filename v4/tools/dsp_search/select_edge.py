#!/usr/bin/env python3
"""Select range-edge demods robustly and against real board IQ (C5VRX).

Re-scores every checkpointed search_edge leader on fresh edge screens (all
must pass the mid/strong sanity limits), then measures the click increase
on real board captures (strong vs weak/static snapshots exported with
capture_iq_snapshot.py). Real IQ has no ground truth: the click measure is
the excess fraction of samples far from their 11-sample median, compared
between the two capture sets and against matched RANGE32/PAIR controls.
"""
import argparse
import glob
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import numpy as np

STATE = {}


def clicks(y):
    y = y[400:]; tip = np.mean(np.sort(y)[:int(.05 * len(y))]); span = np.percentile(y, 99) - tip
    pad = np.pad(y, (5, 5), mode='edge')
    med = np.median(np.lib.stride_tricks.sliding_window_view(pad, 11), axis=1)[:len(y)]
    return float(np.mean(abs(y - med) > .25 * span) * 1000)


def real_click_increase(m, strong, weak):
    import overlay_fsm as O
    f = lambda b: O.decode(b, m)
    return float(np.mean([clicks(f(b)) for b in weak]) - np.mean([clicks(f(b)) for b in strong]))


def setup(seeds):
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
    import search_edge as E
    STATE.update(E=E, screens=[E.screen(s) for s in seeds])


def assess(item):
    import edge_fsm as F
    E = STATE['E']; key, p = item; m = F.synthesize(p); rs = [E.score(m, c) for c in STATE['screens']]
    ok = [r for r in rs if r]
    return dict(id=key, passes=len(ok), score=float(np.mean([r['score'] for r in ok])) if ok else None,
                missed=float(np.mean([r['edge_missed'] for r in ok])) if ok else None,
                large=float(np.mean([r['edge_large'] for r in ok])) if ok else None,
                sync=float(np.mean([r['edge_sync'] for r in ok])) if ok else None)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--search', type=Path, required=True); ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--strong', required=True, help='glob of strong-signal real captures')
    ap.add_argument('--weak', required=True, help='glob of weak/static real captures')
    ap.add_argument('--workers', type=int, default=7); ap.add_argument('--keep', type=int, default=12)
    a = ap.parse_args()
    if a.output.exists(): ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    import engine as S
    import pair_decoder as P
    import search_edge as E
    import edge_fsm as F
    import overlay_fsm as O
    protocol = json.loads((a.search / 'protocol.json').read_text()); base = protocol['config_seed'] - 101
    bank = [None] + [P.learn(l, base + 601 + s, m)[0] for l, m, s in E.DECODERS[1:]]
    items = {}
    for f in sorted(a.search.glob('leaders-*.json')):
        for key, score, genome, metrics in json.loads(f.read_text())['leaders']:
            items.setdefault(key, E.params(genome, bank))
    seeds = [base + 811 + 10 * i for i in range(4)]
    S.save(a.output / 'protocol.json', dict(protocol, select_seeds=seeds, candidates=len(items),
           real_strong=a.strong, real_weak=a.weak,
           rule='all four fresh screens pass sanity; rank by mean edge score; real click increase reported and must not exceed RANGE32',
           select_source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}))
    with mp.get_context('spawn').Pool(a.workers, initializer=setup, initargs=(seeds,)) as pool:
        rows = pool.map(assess, list(items.items()), chunksize=4)
    rows = [r for r in rows if r['passes'] == len(seeds)]
    rows.sort(key=lambda r: -r['score'])
    strong = [np.frombuffer(open(f, 'rb').read(), np.uint8) for f in sorted(glob.glob(a.strong))]
    weak = [np.frombuffer(open(f, 'rb').read(), np.uint8) for f in sorted(glob.glob(a.weak))]
    root = Path(__file__).resolve().parents[2]
    controls = {'RANGE32': json.loads((root / 'tools/range32_model.json').read_text())}
    opts = json.loads((root / 'tools/range_options.json').read_text())['options']
    pair = next((o['model'] for o in opts if o['label'] == 'PAIR RANGE LAB'), None)
    if pair: controls['PAIR'] = pair
    control_rows = {n: real_click_increase(m, strong, weak) for n, m in controls.items()}
    chosen = []
    for r in rows[:60]:
        r['real_click_increase'] = real_click_increase(F.synthesize(items[r['id']]), strong, weak)
        if r['real_click_increase'] <= control_rows['RANGE32']:
            chosen.append(r)
        if len(chosen) >= a.keep: break
    S.save(a.output / 'ranking.json', dict(controls=control_rows, rows=rows[:200]))
    S.save(a.output / 'frozen.json', dict(finalists=[dict(id=r['id'], params=items[r['id']], metrics=r,
                                                         model=F.synthesize(items[r['id']])) for r in chosen],
                                          no_reselection=True))
    summary = dict(candidates=len(items), all_screens=len(rows), finalists=len(chosen), real_controls=control_rows,
                   best=chosen[0] if chosen else None)
    S.save(a.output / 'summary.json', summary); print(json.dumps(summary, default=float), flush=True)


if __name__ == '__main__':
    main()
