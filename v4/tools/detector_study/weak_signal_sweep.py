#!/usr/bin/env python3
"""Separate C/N from ADC occupancy for C5VRX by Twotoz and contributors.

Extends the PR185 synthetic channel and passive DAC models. Diagnostic only:
ADC scaling is not a physical gain setting; Q4 clipping is not fine-lane folding.
No firmware tables, defaults or gain registers are changed.
"""
import argparse
import csv
import json
from pathlib import Path

import numpy as np
import broad_search as B

P, D = B.P, B.D
CNRS = (-2, 0, 2, 4, 6, 8, 10, 12, 14, 18)
RMS = (.75, 1.5, 3., 5.)


def iq_at(sig, noise, cnr, rms):
    """Hold expected total complex IQ power fixed as C/N changes.

    Inputs have unit mean power. Do not normalize each noisy realization:
    that would use test-stream information to set gain.
    """
    ratio = 10 ** (cnr / 10)
    return rms * (sig * np.sqrt(ratio) + noise) / np.sqrt(1 + ratio)


def decode(raw, model):
    if model['name'] == 'HC50':
        phase = P.V.H.endpoint_phases(raw, True, True)
        codes = ((128 + phase[1:] - phase[:-1]) % 256) >> 2
        return D.goggle(np.pad(np.repeat(B.DAC_VOLTS[codes], 2),
                               (0, len(raw) - 2 * len(codes))))
    return B.decode(raw, model)


def calibrate(y, truth):
    """Fit delay/scale/offset on the separate noiseless counterpart only."""
    sl = slice(3000, -3000)
    t = truth[sl]
    best = None
    for lag in range(-5, 7):
        x = np.roll(y, -lag)[sl]
        gain = np.mean((x - x.mean()) * (t - t.mean())) / max(np.var(x), 1e-20)
        offset = t.mean() - gain * x.mean()
        loss = np.mean((gain * x + offset - t) ** 2)
        if best is None or loss < best[0]:
            best = (loss, lag, gain, offset)
    return best[1:]


def score(y, truth, calibration):
    lag, gain, offset = calibration
    sl = slice(3000, -3000)
    error = (gain * np.roll(y, -lag) + offset - truth)[sl]
    mse = np.mean(error ** 2)  # Includes DC/level error; never re-fit on noisy truth.
    sinad = 10 * np.log10(max(np.var(truth[sl]), 1e-20) / max(mse, 1e-20))
    return float(sinad), float(1000 * np.mean(abs(error) > 40))


def occupancy(raw):
    i, q = D.cells(raw[3000:-3000])
    return dict(origin_permille=float(1000 * np.mean((i >= -1) & (i <= 0) &
                                                   (q >= -1) & (q <= 0))),
                rail_permille=float(1000 * np.mean((i == -8) | (i == 7) |
                                                 (q == -8) | (q == 7))))


def contrast(y, truth, calibration):
    """Coherent video amplitude relative to its noiseless calibration (percent).

    This does not measure PAL/NTSC chroma or goggle saturation.
    """
    lag, gain, _ = calibration
    x, t = np.roll(y, -lag)[3000:-3000], truth[3000:-3000]
    return float(100 * gain * np.mean((x-x.mean()) * (t-t.mean())) /
                 max(np.var(t), 1e-20))


def aggregate(rows):
    curves = []
    for model in sorted({r['model'] for r in rows}):
        for kind in sorted({r['kind'] for r in rows}):
            for rms in RMS:
                points = []
                for cnr in CNRS:
                    group = [r for r in rows if (r['model'], r['kind'], r['rms'], r['cnr']) ==
                             (model, kind, rms, cnr)]
                    points.append(dict(cnr=cnr, **{k: float(np.mean([r[k] for r in group]))
                                                  for k in ('sinad', 'clicks', 'fitted_sinad',
                                                            'contrast_percent', 'origin_permille', 'rail_permille')}))
                # Illustrative numerical gate, NOT a PAL/NTSC or goggle lock criterion.
                # Require every tested higher C/N to pass; do not assume monotonicity.
                threshold = next((p['cnr'] for j, p in enumerate(points)
                                  if all(q['sinad'] >= 6 and q['clicks'] <= 10
                                         for q in points[j:])), None)
                curves.append(dict(model=model, kind=kind, rms=rms,
                                   illustrative_threshold_cnr=threshold, points=points))
    return curves


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--seeds', nargs='+', type=int, default=[1801, 1802, 1803])
    ap.add_argument('--samples', type=int, default=65536,
                    help='80-MS/s source samples (even, >=32768)')
    args = ap.parse_args()
    if args.samples < 32768 or args.samples % 2:
        ap.error('--samples must be even and >=32768')
    args.output.mkdir(parents=True, exist_ok=True)
    models = []
    for name in ('VLP56', 'OVP56'):
        m = json.loads((P.ROOT / 'tools' / (name.lower() + '_codebook.json')).read_text())
        m.update(name=name, current_tokens=56, previous_shift=1, middle_bits=[])
        models.append(m)
    models.append(dict(name='HC50'))
    rows = []
    for seed in args.seeds:
        # No injected DC/skew here: isolate C/N and ADC occupancy first.
        for kind in ('random', 'bars', 'multitone'):
            sig, noise, _, ire = P.stream(seed, n=args.samples, kind=kind)
            truth = D.goggle(ire)
            for rms in RMS:
                for cnr in CNRS:
                    raw = D.raw_bytes(iq_at(sig, noise, cnr, rms)).astype(np.uint8)
                    clean = D.raw_bytes(iq_at(sig, np.zeros_like(noise), cnr, rms)).astype(np.uint8)
                    occ = occupancy(raw)
                    for model in models:
                        y = decode(raw, model)
                        cal = calibrate(decode(clean, model), truth)
                        sinad, clicks = score(y, truth, cal)
                        fitted_sinad, _ = P.quick_score(y, truth)
                        rows.append(dict(seed=seed, kind=kind, rms=rms, cnr=cnr,
                                         model=model['name'], sinad=sinad, clicks=clicks,
                                         fitted_sinad=fitted_sinad,
                                         contrast_percent=contrast(y, truth, cal), **occ))
            print(f'Completed seed={seed} kind={kind}', flush=True)
    summary = dict(seeds=args.seeds, source_samples=args.samples, cnrs=CNRS, rms=RMS,
                   objective='diagnose weak-C/N collapse separately from ADC occupancy; no candidate selection',
                   calibration='per-case noiseless delay/gain/offset, frozen before noisy scoring',
                   illustrative_gate='SINAD >=6dB and >40IRE errors <=10/1000 at this and every higher tested C/N',
                   scope='AWGN synthetic monochrome/detail channel; clipped Q4; passive DAC model; no PHY gain/noise figure/folding/multipath/sync lock/RF sensitivity proof',
                   curves=aggregate(rows))
    (args.output / 'measurements.json').write_text(json.dumps(rows, allow_nan=False) + '\n')
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    with (args.output / 'curves.csv').open('w', newline='') as f:
        fields = ['model', 'kind', 'rms', 'cnr', 'sinad', 'clicks', 'contrast_percent',
                  'fitted_sinad', 'origin_permille', 'rail_permille']
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for curve in summary['curves']:
            for point in curve['points']:
                writer.writerow(dict(model=curve['model'], kind=curve['kind'], rms=curve['rms'], **point))
    for kind in ('random', 'bars', 'multitone'):
        for model in models:
            curves = [c for c in summary['curves'] if c['kind'] == kind and c['model'] == model['name']]
            print(kind, model['name'], [(c['rms'], c['illustrative_threshold_cnr']) for c in curves])


if __name__ == '__main__':
    main()
