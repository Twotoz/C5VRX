#!/usr/bin/env python3
"""APEX step 1: EDGE transitions + learned state/observation DAC6 (C5VRX).

PR #190 review (Twotoz, 2026-10-09) suggested a single continuously running
program whose 6-bit output is a learned function of (state, token) rather
than the quantized frequency state alone. This keeps the pinned EDGE
transitions (weak-signal sync) and fits one DAC code per LUT word by
filtered-CVBS least squares (extends reconstruction.py) across randomized,
AFC-centred VTX/boards, each case decoded with its own AutoFit transitions.
EDGE AutoFit rescales grid and output together, so a per-word video level
transfers across fits. Evaluation uses held-out seeds against PAIR+AutoFit,
EDGE and the switched FusionDemod.
"""
import argparse
import json
from pathlib import Path
import numpy as np
import scipy.signal as sg
from scipy.sparse.linalg import LinearOperator, lsmr


def fit_shared(models, cases, strong_weight=4., regularization=.3, iterations=80, edge_weight=1.):
    import overlay_fsm as O
    import engine as S
    import search_edge as E
    k = 1024
    # Fit against each case aligned to this model's own latency (the shared
    # reference lag is off by up to a span for LUT models; 2026-10-09).
    cases = [E.clamp(c, O.decode(c['raw'], m)) for m, c in zip(models, cases)]
    ix = [O.model_indices(c['raw'], m, np.array(m['lut'], np.uint16)) for m, c in zip(models, cases)]
    weights = [np.sqrt(strong_weight if c['cnr'] >= 16 else 1.) for c in cases]
    norm = np.sqrt(np.maximum(sum(w * w * np.bincount(a, minlength=k) for a, w in zip(ix, weights)), 1))
    windows, targets, sws = [], [], []
    unit = 63 / O.H.B.DAC_VOLTS[-1]
    for c, w in zip(cases, weights):
        lag, gain, offset = c['calibration']; n = len(c['raw'])
        lo = max(3000, -lag); hi = min(n - 3000, n - lag); windows.append((lo + lag, hi + lag))
        edges = (np.abs(np.gradient(c['truth'])) > .3) | (c['region'] == 2)
        sw = np.sqrt(1 + (edge_weight - 1) * edges[lo:hi]); sws.append(sw)
        targets.append(w * sw * (c['truth'][lo:hi] - offset) / gain * unit)
    sizes = [b - a for a, b in windows]; total = sum(sizes); rr = np.sqrt(regularization)

    def forward(z):
        x = z / norm; out = []
        for a, w, (lo, hi), c, sw in zip(ix, weights, windows, cases, sws):
            y = np.zeros(len(c['raw'])); y[2:] = np.repeat(x[a], 2)
            out.append(w * sw * O.H.B.D.goggle(y)[lo:hi])
        return np.concatenate(out + [rr * z])

    def reverse(y):
        acc = np.zeros(k); off = 0
        for a, w, (lo, hi), size, c, sw in zip(ix, weights, windows, sizes, cases, sws):
            yy = np.zeros(len(c['raw'])); yy[lo:hi] = w * sw * y[off:off + size]; off += size
            yy = sg.lfilter(*O.H.B.D.GOG, yy[::-1])[::-1][2:].reshape(-1, 2).sum(1)
            acc += np.bincount(a, weights=yy, minlength=k)
        return acc / norm + rr * y[total:total + k]

    op = LinearOperator((total + k, k), matvec=forward, rmatvec=reverse)
    target = np.concatenate(targets + [np.zeros(k)])
    base = S.F.LEVELS[np.array(models[0]['lut']) & 63]
    res = target - op.matvec(base * norm); res[total:] = 0
    sol = lsmr(op, res, atol=2e-5, btol=2e-5, maxiter=iterations)
    return np.clip(base + sol[0] / norm, 0, 63)


def with_dac(m, values):
    import engine as S
    lut = np.array(m['lut'], np.uint16)
    codes = S.F.nearest(values).astype(np.uint16)
    return dict(m, lut=((lut & 0xffc0) | codes).tolist())


def evaluate(cases, controls):
    import overlay_fsm as O
    import search_edge as E
    from video_metrics import waveform
    rows = {}
    for c in cases:
        for name, make in controls.items():
            m = make(c); y = O.decode(c['raw'], m)
            try:
                r = waveform(y, E.clamp(c, y))
                v = (r['h_missing'] + r['v_missing'], r['sinad'], r['false_sync_per_line'])
            except Exception:
                v = (30, -20., 1.)
            rows.setdefault((c['cnr'], name), []).append(v)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--train-seeds', type=int, nargs='+', default=[451000, 452000])
    ap.add_argument('--test-seeds', type=int, nargs='+', default=[461000, 462000])
    ap.add_argument('--strong-weight', type=float, default=4.)
    ap.add_argument('--regularization', type=float, default=.3)
    ap.add_argument('--edge-weight', type=float, default=1.)
    a = ap.parse_args()
    if a.output.exists(): ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    import ladder_edge as LE
    import edge_fsm as F
    from pair_autofit import remap
    root = Path(__file__).resolve().parents[2]
    opts = json.loads((root / 'tools/range_options.json').read_text())['options']
    edge = next(o for o in opts if o['label'] == 'EDGE RANGE LAB')['model']['params']
    pair = next(o for o in opts if o['label'] == 'PAIR RANGE LAB')['model']
    ep = {k: v for k, v in edge.items() if k not in ('fit_deviation', 'fit_centre_hz')}
    synth = lambda c: F.synthesize(dict(ep, **c['fit']))
    train = [c for s in a.train_seeds for c in LE.cases(s, per=4, afc=True)]
    values = fit_shared([synth(c) for c in train], train, a.strong_weight, a.regularization, edge_weight=a.edge_weight)
    np.save(a.output / 'dac_values.npy', values)
    test = [c for s in a.test_seeds for c in LE.cases(s, per=6, afc=True)]
    pair_af = lambda c: remap(pair, c['fit']['fit_deviation'], c['fit']['fit_centre_hz'])
    controls = {'PAIR+AF': pair_af, 'EDGE+AF': synth, 'APEX-DAC': lambda c: with_dac(synth(c), values)}
    rows = evaluate(test, controls)
    for c in LE.CNR:
        rows[(c, 'FUSION')] = rows[(c, 'PAIR+AF')] if c >= 9 else rows[(c, 'EDGE+AF')]
        rows[(c, 'FUSION-APEX')] = rows[(c, 'PAIR+AF')] if c >= 9 else rows[(c, 'APEX-DAC')]
    names = ('PAIR+AF', 'EDGE+AF', 'APEX-DAC', 'FUSION', 'FUSION-APEX')
    table = {f'{c}/{n}': dict(missed=int(sum(v[0] for v in rows[(c, n)])),
                              sinad=float(np.mean([v[1] for v in rows[(c, n)]])),
                              false=float(np.mean([v[2] for v in rows[(c, n)]])))
             for c in LE.CNR for n in names}
    (a.output / 'apex_dac.json').write_text(json.dumps(dict(
        train_seeds=a.train_seeds, test_seeds=a.test_seeds, strong_weight=a.strong_weight,
        regularization=a.regularization, edge_weight=a.edge_weight, table=table), indent=1))
    print('C/N | ' + ' | '.join(f'{n} miss/SINAD/false' for n in names))
    for c in LE.CNR:
        print(f'{c:3d} | ' + ' | '.join('%3d/%4.1f/%.2f' % (table[f'{c}/{n}']['missed'], table[f'{c}/{n}']['sinad'],
                                                            table[f'{c}/{n}']['false']) for n in names), flush=True)


if __name__ == '__main__':
    main()
