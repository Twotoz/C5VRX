#!/usr/bin/env python3
"""CVT FusionDemod: one EDGE state machine, continuously blended DAC (C5VRX).

Operator 2026-10-09: no two-gear gearbox; a smooth, continuously variable
fusion. The EDGE transitions (sync-safe everywhere) stay fixed; only the
6-bit output per LUT word moves along alpha in [0, 1] between the robust
frequency-level output (alpha 0, click-safe) and the learned reconstruction
of the same (state, token) (alpha 1, apex_dac fit, PAIR-like detail).
alpha follows the measured phase C/N. This scores every alpha per C/N on
held-out randomized cases, derives the alpha(C/N) curve, and compares the
CVT against PAIR+AF, EDGE+AF and the two-gear FusionDemod.
"""
import argparse
import json
from pathlib import Path
import numpy as np

ALPHAS = (0., .15, .3, .45, .6, .8, 1.)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--dac', type=Path, default=Path.home() / 'c5vrx4-apex-1-.3-4/dac_values.npy')
    a = ap.parse_args()
    if a.output.exists(): ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    import ladder_edge as LE
    import edge_fsm as F
    import apex_dac as AD
    import engine as S
    import phase_cnr as PC
    from pair_autofit import remap
    root = Path(__file__).resolve().parents[2]
    opts = json.loads((root / 'tools/range_options.json').read_text())['options']
    ep = {k: v for k, v in next(o for o in opts if o['label'] == 'EDGE RANGE LAB')['model']['params'].items()
          if k not in ('fit_deviation', 'fit_centre_hz')}
    pair = next(o for o in opts if o['label'] == 'PAIR RANGE LAB')['model']
    learned = np.load(a.dac)

    def blend(alpha):
        def make(c):
            m = F.synthesize(dict(ep, **c['fit'])); base = S.F.LEVELS[np.array(m['lut']) & 63]
            return AD.with_dac(m, base + alpha * (learned - base))
        return make

    test = [c for s in (471000, 472000, 473000) for c in LE.cases(s, per=4, afc=True)]
    controls = {f'a{al}': blend(al) for al in ALPHAS}
    controls['PAIR+AF'] = lambda c: remap(pair, c['fit']['fit_deviation'], c['fit']['fit_centre_hz'])
    rows = AD.evaluate(test, controls)
    cnr_meas = {c['cnr']: [] for c in test}
    for c in test:
        cnr_meas[c['cnr']].append(np.median([PC.phase_cnr_x10(c['raw'][j:j + 4096]) / 10 for j in range(0, len(c['raw']), 4096)]))
    agg = lambda n, k: (sum(v[0] for v in rows[(k, n)]), float(np.mean([v[1] for v in rows[(k, n)]])),
                        float(np.mean([v[2] for v in rows[(k, n)]])))
    # Goggle score per C/N: SINAD minus heavy penalties for lost and false sync.
    score = lambda t: t[1] - 1.5 * t[0] / 8 - 15 * t[2]
    table = {}
    print('C/N  phaseC/N | ' + ' | '.join(f'a={al}' for al in ALPHAS) + ' | PAIR+AF   [missed/SINAD/false]')
    for k in LE.CNR:
        cells = {f'a{al}': agg(f'a{al}', k) for al in ALPHAS}; cells['PAIR+AF'] = agg('PAIR+AF', k)
        best = max((f'a{al}' for al in ALPHAS), key=lambda n: score(cells[n]))
        table[k] = dict(phase_cnr=float(np.median(cnr_meas[k])), cells=cells, best_alpha=float(best[1:]))
        print(f'{k:3d}  {np.median(cnr_meas[k]):5.1f}   | ' + ' | '.join('%d/%.1f/%.2f' % cells[f'a{al}'] for al in ALPHAS)
              + ' | %d/%.1f/%.2f   best alpha %s' % (cells['PAIR+AF'] + (best[1:],)), flush=True)
    (a.output / 'cvt_blend.json').write_text(json.dumps({str(k): v for k, v in table.items()}, indent=1))


if __name__ == '__main__':
    main()
