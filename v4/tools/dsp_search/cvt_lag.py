#!/usr/bin/env python3
"""Fixed-lag smoothing for the CVT detail output (C5VRX by Twotoz/contributors).

PR #190 review (APEX-LAG): the word emitted at span k+L has seen the
observations up to k+L, so fitting its 6-bit output to the video at span k
turns the causal estimate into a fixed-lag smoother, a free gain in
estimation theory, at L x 50 ns extra latency and no hardware change. The
truth is delayed by L spans for fitting and scoring (the scorer's fixed
calibration lag otherwise favours lower latency; see the latency
correction in PAIR_RANGE_STUDY.md). Held-out randomized cases, deviation
0.6-1.4, CVT best alpha per C/N, with the real-IQ click check.
"""
import argparse
import json
from pathlib import Path
import numpy as np

ALPHAS = (0., .5, 1.)


def delayed(c, L):
    if not L: return c
    d = dict(c)
    for k in ('truth', 'region'):
        v = np.array(c[k]); d[k] = np.concatenate([np.repeat(v[:1], 2 * L), v[:-2 * L]])
    lag, g, off = c['calibration']; d['calibration'] = (lag, g, off)
    return d


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
    import select_edge as SE
    root = Path(__file__).resolve().parents[2]
    opts = json.loads((root / 'tools/range_options.json').read_text())['options']
    ep = {k: v for k, v in next(o for o in opts if o['label'] == 'EDGE RANGE LAB')['model']['params'].items()
          if k not in ('fit_deviation', 'fit_centre_hz')}
    synth = lambda c: F.synthesize(dict(ep, **c['fit']))
    train = [c for s in (451000, 452000) for c in LE.cases(s, per=4, afc=True)]
    test = [c for s in (481000, 482000, 483000) for c in LE.cases(s, per=4, afc=True)]
    strong = [np.fromfile(f, np.uint8) for f in sorted((Path.home() / 'c5vrx4-static-224006').glob('*.bin'))]
    weak = [np.fromfile(f, np.uint8) for f in sorted((Path.home() / 'c5vrx4-static-224103').glob('*.bin'))]
    out = {}
    for L in (0, 1, 2):
        tr = [delayed(c, L) for c in train]; te = [delayed(c, L) for c in test]
        learned = AD.fit_shared([synth(c) for c in tr], tr, 4., .3, edge_weight=4.)

        def blend(alpha):
            def make(c):
                m = synth(c); base = S.F.LEVELS[np.array(m['lut']) & 63]
                return AD.with_dac(m, base + alpha * (learned - base))
            return make
        r = AD.evaluate(te, {f'a{al}': blend(al) for al in ALPHAS})
        best = {}
        for k in LE.CNR:
            cells = {al: (sum(v[0] for v in r[(k, f'a{al}')]), float(np.mean([v[1] for v in r[(k, f'a{al}')]])),
                          float(np.mean([v[2] for v in r[(k, f'a{al}')]]))) for al in ALPHAS}
            al = max(ALPHAS, key=lambda x: cells[x][1] - 1.5 * cells[x][0] / 8 - 15 * cells[x][2])
            best[k] = (al,) + cells[al]
        m1 = blend(1.)({'fit': dict(fit_deviation=.69, fit_centre_hz=-436e3 + .69 * 1436e3)})
        clicks = SE.real_click_increase(m1, strong, weak)
        out[L] = dict(best={str(k): v for k, v in best.items()}, alpha1_real_clicks=clicks)
        np.save(a.output / f'learned_lag{L}.npy', learned)
        print(f'lag {L} span(s) | ' + ' | '.join('%d:%d/%.1f' % (k, best[k][1], best[k][2]) for k in LE.CNR)
              + f' | alpha1 clicks {clicks:.2f}', flush=True)
    (a.output / 'cvt_lag.json').write_text(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
