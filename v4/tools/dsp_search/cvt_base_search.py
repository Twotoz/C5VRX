#!/usr/bin/env python3
"""Which tracker base and detail output make the best single CVT? (C5VRX)

Operator 2026-10-09: one model that keeps video at every level, sharp when
the signal allows. The CVT keeps one state machine and glides its output;
its weak point is strong signal (alpha 1 at 7-8 dB SINAD vs PAIR 9-10), so
a PAIR top gear was still needed. This tries tracker bases with more
phase states (4x32, 8x16, 16x8 phases x frequencies, 8 tokens, same
program) and detail outputs fitted on all C/N or on strong cases only,
then scores the CVT (best alpha per C/N) on held-out randomized cases
(deviation 0.6-1.4) next to PAIR+AutoFit.
"""
import argparse
import json
from pathlib import Path
import numpy as np

ALPHAS = (0., .3, .6, 1.)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    if a.output.exists(): ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    import ladder_edge as LE
    import edge_fsm as F
    import apex_dac as AD
    import engine as S
    from pair_autofit import remap
    root = Path(__file__).resolve().parents[2]
    opts = json.loads((root / 'tools/range_options.json').read_text())['options']
    ep = {k: v for k, v in next(o for o in opts if o['label'] == 'EDGE RANGE LAB')['model']['params'].items()
          if k not in ('fit_deviation', 'fit_centre_hz')}
    pair = next(o for o in opts if o['label'] == 'PAIR RANGE LAB')['model']
    train = [c for s in (451000, 452000) for c in LE.cases(s, per=4, afc=True)]
    test = [c for s in (481000, 482000, 483000) for c in LE.cases(s, per=4, afc=True)]
    bases = {'4x32': dict(ep), '8x16': dict(ep, phases=8, frequencies=16, kp=1., ki=.15),
             '16x8': dict(ep, phases=16, frequencies=8, kp=1., ki=.2)}
    rows = AD.evaluate(test, {'PAIR+AF': lambda c: remap(pair, c['fit']['fit_deviation'], c['fit']['fit_centre_hz'])})
    result = {}
    for bname, bp in bases.items():
        synth = (lambda p: lambda c: F.synthesize(dict(p, **c['fit'])))(bp)
        for fname, sel in (('all', lambda c: True), ('strong', lambda c: c['cnr'] >= 10)):
            tr = [c for c in train if sel(c)]
            learned = AD.fit_shared([synth(c) for c in tr], tr, 4., .3, edge_weight=4.)

            def blend(alpha):
                def make(c):
                    m = synth(c); base = S.F.LEVELS[np.array(m['lut']) & 63]
                    return AD.with_dac(m, base + alpha * (learned - base))
                return make
            r = AD.evaluate(test, {f'a{al}': blend(al) for al in ALPHAS})
            best = {}
            for k in LE.CNR:
                cells = {al: (sum(v[0] for v in r[(k, f'a{al}')]), float(np.mean([v[1] for v in r[(k, f'a{al}')]])),
                              float(np.mean([v[2] for v in r[(k, f'a{al}')]]))) for al in ALPHAS}
                al = max(ALPHAS, key=lambda x: cells[x][1] - 1.5 * cells[x][0] / 8 - 15 * cells[x][2])
                best[k] = (al,) + cells[al]
            result[f'{bname}/{fname}'] = best
            print(f'{bname:5s} detail-fit {fname:6s} | ' + ' | '.join(
                '%d:%d/%.1f' % (k, best[k][1], best[k][2]) for k in LE.CNR), flush=True)
    pr = {k: (sum(v[0] for v in rows[(k, 'PAIR+AF')]), float(np.mean([v[1] for v in rows[(k, 'PAIR+AF')]]))) for k in LE.CNR}
    print('PAIR+AF              | ' + ' | '.join('%d:%d/%.1f' % (k, pr[k][0], pr[k][1]) for k in LE.CNR), flush=True)
    (a.output / 'cvt_base.json').write_text(json.dumps(dict(cvt={k: {str(c): v for c, v in d.items()} for k, d in result.items()},
                                                            pair={str(k): v for k, v in pr.items()}), indent=1))


if __name__ == '__main__':
    main()
