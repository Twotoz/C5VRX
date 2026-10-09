#!/usr/bin/env python3
"""APEX three-tier information study (C5VRX by Twotoz/contributors).

PR #190 review (Twotoz, 2026-10-09): measure the causal teacher ->
quantized LUT -> fixed baseline gap on the same IQ before searching for
more. Tier A is a causal floating-point PLL (ablate_pll.trace, the tuned
'pll' theory winner) on the same quantized Q4/I4 at every 40 MS/s sample,
with the same AutoFit output remap, optionally re-tuned per C/N (an
adaptive upper bound). Tier B/C are LUT models on the identical cases.
Never a claimed C5 schedule; Tier A shows what the IQ still contains.
"""
import argparse
import itertools
import json
from pathlib import Path
import numpy as np


def teacher_decode(raw, p, dev, centre, stride=1):
    import ablate_pll as A
    import engine as S
    freq, _, _ = A.trace(raw, p, stride=stride)
    cn = 2 * np.pi * 1e6 / 20e6; cm = 2 * np.pi * centre / 20e6
    # trace() runs at 40 MS/s (rad per 25 ns); the DAC transfer is per 50 ns.
    out = cn + (2 * freq / stride - cm) / dev
    code = np.rint(np.clip((out * 128 / np.pi - S.H.B.P.OFFSET) * S.H.B.P.SCALE, 0, 63)).astype(int)
    # Same latency as the LUT executor (2 samples): a fixed calibration lag
    # otherwise favours the lower-latency teacher (found 2026-10-09).
    y = np.zeros(len(raw)); y[2:] = np.repeat(S.H.B.DAC_VOLTS[code], stride)[:len(raw) - 2]
    return S.H.B.D.goggle(y)


def score(y, c):
    import search_edge as E
    from video_metrics import waveform
    try:
        r = waveform(y, E.clamp(c, y))
        return r['h_missing'] + r['v_missing'], r['sinad'], r['false_sync_per_line']
    except Exception:
        return 30, -20., 1.


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--test-seeds', type=int, nargs='+', default=[461000, 462000])
    a = ap.parse_args()
    if a.output.exists(): ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    import ladder_edge as LE
    import edge_fsm as F
    import overlay_fsm as O
    from pair_autofit import remap
    root = Path(__file__).resolve().parents[2]
    hyp = json.loads((root / 'tools/detector_study/models/theory_hypotheses.json').read_text())
    p0 = np.array(next(m for m in hyp['winners'] if m['family'] == 'pll')['params'], float)
    opts = json.loads((root / 'tools/range_options.json').read_text())['options']
    edge = next(o for o in opts if o['label'] == 'EDGE RANGE LAB')['model']['params']
    pair = next(o for o in opts if o['label'] == 'PAIR RANGE LAB')['model']
    ep = {k: v for k, v in edge.items() if k not in ('fit_deviation', 'fit_centre_hz')}
    test = [c for s in a.test_seeds for c in LE.cases(s, per=6, afc=True)]
    # Adaptive teacher: loop gains scaled per C/N (kp, ki x {.5, 1, 2}), best per C/N.
    scales = list(itertools.product((.5, 1., 2.), (.5, 1., 2.)))
    rows = {}
    for c in test:
        d, ce = c['fit']['fit_deviation'], c['fit']['fit_centre_hz']
        add = lambda n, v: rows.setdefault((c['cnr'], n), []).append(v)
        add('A-teacher', score(teacher_decode(c['raw'], p0, d, ce), c))
        for sk, si in scales:
            p = p0.copy(); p[0] *= sk; p[1] *= si
            add(f'A-scale{sk}/{si}', score(teacher_decode(c['raw'], p, d, ce), c))
        add('B-EDGE+AF', score(O.decode(c['raw'], F.synthesize(dict(ep, **c['fit']))), c))
        add('C-PAIR+AF', score(O.decode(c['raw'], remap(pair, d, ce)), c))
    agg = lambda n, cnr: (int(sum(v[0] for v in rows[(cnr, n)])), float(np.mean([v[1] for v in rows[(cnr, n)]])),
                          float(np.mean([v[2] for v in rows[(cnr, n)]])))
    table = {}
    for cnr in LE.CNR:
        best = max((f'A-scale{sk}/{si}' for sk, si in scales),
                   key=lambda n: agg(n, cnr)[1] - 2 * agg(n, cnr)[0] / 12 - 10 * agg(n, cnr)[2])
        table[cnr] = {n: agg(n, cnr) for n in ('A-teacher', best, 'B-EDGE+AF', 'C-PAIR+AF')}
        table[cnr]['adaptive_best'] = best
    (a.output / 'tiers.json').write_text(json.dumps({str(k): v for k, v in table.items()}, indent=1))
    print('C/N | A teacher | A adaptive (scale) | B EDGE+AF | C PAIR+AF   [missed / SINAD / false]')
    for cnr in LE.CNR:
        t = table[cnr]; b = t['adaptive_best']
        f = lambda v: '%3d/%4.1f/%.2f' % v
        print(f'{cnr:3d} | {f(t["A-teacher"])} | {f(t[b])} ({b[7:]}) | {f(t["B-EDGE+AF"])} | {f(t["C-PAIR+AF"])}', flush=True)


if __name__ == '__main__':
    main()
