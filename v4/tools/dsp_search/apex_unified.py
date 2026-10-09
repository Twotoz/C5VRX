#!/usr/bin/env python3
"""APEX 2.0: one self-fitting LUT for both regimes (C5VRX by Twotoz/contributors).

apex_explore.py found that 8 phases x 32 frequencies x 4 tokens (8 state
bits + 2 token bits, legal in the shared-word schedule: 6+8+2 = 16)
approaches PAIR detail and EDGE weak sync in one quantized second-order
loop with frequency output (no output innovation: real-IQ click veto).
This builds actual LUTs with edge_fsm (PAIR4411 learned tokens, AutoFit),
tunes on training seeds, and compares on held-out seeds and real board IQ
with PAIR+AutoFit, EDGE+AutoFit and the switched FusionDemod.
"""
import argparse
import glob
import itertools
import json
from pathlib import Path
import numpy as np


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--train-seeds', type=int, nargs='+', default=[451000, 452000])
    ap.add_argument('--test-seeds', type=int, nargs='+', default=[471000, 472000, 473000])
    a = ap.parse_args()
    if a.output.exists(): ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    import ladder_edge as LE
    import edge_fsm as F
    import apex_dac as AD
    import select_edge as SE
    import pair_decoder as PD
    from pair_autofit import remap
    root = Path(__file__).resolve().parents[2]
    opts = json.loads((root / 'tools/range_options.json').read_text())['options']
    edge = next(o for o in opts if o['label'] == 'EDGE RANGE LAB')['model']['params']
    pair = next(o for o in opts if o['label'] == 'PAIR RANGE LAB')['model']
    ep = {k: v for k, v in edge.items() if k not in ('fit_deviation', 'fit_centre_hz')}
    banks = {'weak': ep['observation_vectors'], 'uniform': PD.learn('4411', 431601, 'uniform')[0]}
    grid = []
    for (P, Fq, T), kp, ki, det, bank in itertools.product(((8, 32, 4), (4, 32, 8), (16, 16, 4)), (.7, 1., 1.3),
                                                          (.1, .15, .2, .3), ('clip', 'tanh'), ('weak', 'uniform')):
        grid.append(dict(ep, phases=P, frequencies=Fq, token_bits=int(T).bit_length() - 1, kp=kp, ki=ki,
                         detector=det, reliability=False, observation_vectors=banks[bank], bank=bank))
    train = [c for s in a.train_seeds for c in LE.cases(s, per=3, afc=True) if c['cnr'] in (2, 4, 6, 8, 13, 20, 30)]

    def objective(p, cases):
        rows = AD.evaluate(cases, {'m': lambda c: F.synthesize(dict(p, **c['fit']))})
        weak = sum(v[0] for k in (2, 4, 6) for v in rows[(k, 'm')])
        false = np.mean([v[2] for k in (2, 4, 6) for v in rows[(k, 'm')]])
        strong = np.mean([v[1] for k in (13, 20, 30) for v in rows[(k, 'm')]])
        mid = np.mean([v[1] for v in rows[(8, 'm')]])
        return strong + .5 * mid - .5 * weak - 10 * false

    scored = sorted(((objective(p, train), i) for i, p in enumerate(grid)), reverse=True)
    best = [grid[i] for _, i in scored[:4]]
    strong_cap = [np.fromfile(f, np.uint8) for f in sorted(glob.glob(str(Path.home() / 'c5vrx4-static-224006/*.bin')))]
    weak_cap = [np.fromfile(f, np.uint8) for f in sorted(glob.glob(str(Path.home() / 'c5vrx4-static-224103/*.bin')))]
    test = [c for s in a.test_seeds for c in LE.cases(s, per=4, afc=True)]
    synth = lambda p: (lambda c: F.synthesize(dict(p, **c['fit'])))
    controls = {'PAIR+AF': lambda c: remap(pair, c['fit']['fit_deviation'], c['fit']['fit_centre_hz']),
                'EDGE+AF': synth(ep)}
    for k, p in enumerate(best):
        controls[f'U{k}'] = synth(p)
    rows = AD.evaluate(test, controls)
    for c in LE.CNR:
        rows[(c, 'FUSION')] = rows[(c, 'PAIR+AF')] if c >= 9 else rows[(c, 'EDGE+AF')]
    names = list(controls) + ['FUSION']
    clicks = {'PAIR+AF': SE.real_click_increase(remap(pair, 1.23, 1.96e6), strong_cap, weak_cap),
              'EDGE+AF': SE.real_click_increase(F.synthesize(dict(ep, fit_deviation=1.23, fit_centre_hz=1.96e6)), strong_cap, weak_cap)}
    for k, p in enumerate(best):
        clicks[f'U{k}'] = SE.real_click_increase(F.synthesize(dict(p, fit_deviation=1.23, fit_centre_hz=1.96e6)), strong_cap, weak_cap)
    table = {f'{c}/{n}': dict(missed=int(sum(v[0] for v in rows[(c, n)])), sinad=float(np.mean([v[1] for v in rows[(c, n)]])),
                              false=float(np.mean([v[2] for v in rows[(c, n)]]))) for c in LE.CNR for n in names}
    describe = {f'U{k}': {x: p[x] for x in ('phases', 'frequencies', 'token_bits', 'kp', 'ki', 'detector', 'bank')}
                for k, p in enumerate(best)}
    (a.output / 'unified.json').write_text(json.dumps(dict(candidates=describe, real_click_increase=clicks, table=table,
                                                           train_seeds=a.train_seeds, test_seeds=a.test_seeds), indent=1))
    for n, d in describe.items(): print(n, d, 'real clicks %.2f' % clicks[n])
    print('real clicks PAIR+AF %.2f EDGE+AF %.2f' % (clicks['PAIR+AF'], clicks['EDGE+AF']))
    print('C/N | ' + ' | '.join(f'{n} miss/SINAD/false' for n in names))
    for c in LE.CNR:
        print(f'{c:3d} | ' + ' | '.join('%3d/%4.1f/%.2f' % (table[f'{c}/{n}']['missed'], table[f'{c}/{n}']['sinad'],
                                                            table[f'{c}/{n}']['false']) for n in names), flush=True)


if __name__ == '__main__':
    main()
