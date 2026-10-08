#!/usr/bin/env python3
"""Geometry controls for C5VRX's frozen multi-theory experiment.

Extends the existing all-sample adjacent versus endpoint/trajectory research.
No candidate selection or refitting; uses the already frozen theory winners.
Separates unique40 from pair20 output, while retaining acquired IQ40 for both.
Every new all-sample estimator remains an offline reference.
"""
import argparse
import json
from pathlib import Path
import numpy as np
import mega_demod_bench as M

F, W, T = M.F, M.W, M.T


def decode(raw, m):
    if 'control' not in m:
        return M.decode(raw, m)
    i, q = F.D.cells(raw)
    z = i+.5+1j*(q+.5)
    if m['control'] == 'endpoint50':
        v = z[2::2]*z[:-2:2].conjugate()
        freq = np.pad(np.repeat(np.angle(v)/2, 2), (0, 2))
    else:
        freq = T.estimate(z, m)
        if m['control'] == 'pair20':
            freq = np.repeat((freq[::2]+freq[1::2])/2, 2)
    code = np.rint(np.clip((freq*256/np.pi-F.P.OFFSET)*F.P.SCALE, 0, 63)).astype(int)
    return F.D.goggle(F.B.DAC_VOLTS[code])


def measure(m, c):
    y, clean = decode(c['raw'], m), decode(c['clean'], m)
    cal = M.clean_calibration(clean, c['truth'], 3000)
    return dict(model=m['name'], case_id=c.get('case_id', f"channel/{c['seed']}/{c['kind']}/{c['cnr']}/{c['rms']}"), seed=c['seed'], kind=c['kind'], cnr=c['cnr'], rms=c['rms'],
                **M.metrics(y, c['truth'], cal, 3000))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--frozen-theories', type=Path, required=True)
    ap.add_argument('--frozen-lut', type=Path, required=True)
    a = ap.parse_args()
    if (a.output/'protocol.json').exists():
        ap.error('use a fresh evidence directory')
    a.output.mkdir(parents=True, exist_ok=True)
    fixed = dict(family='delay', params=[1., 1., 0., 0., 0., 0.])
    models = [dict(fixed, name='adjacent40-control', control='unique40'),
              dict(fixed, name='adjacent-pair20-control', control='pair20'),
              dict(fixed, name='endpoint50-control', control='endpoint50')]
    frozen = json.loads(a.frozen_theories.read_text())
    seeds = frozen['seed_policy']
    advanced_guard = any('max_strong_contrast_error_increase' in s for s in frozen['selection'].values())
    for m in frozen['winners']:
        models.append(dict(m, name=m['name']+'-pair20', control='pair20'))
    models += [F.load_reference('OVP56'), F.load_reference('VLP56'), dict(name='HC50'),
               json.loads(a.frozen_lut.read_text())['winner']]
    M.S.write_json(a.output/'protocol.json', dict(models=models, seeds=seeds,
                                                policy='fixed geometry controls; no ranking, reselection or refit'))
    for name, cases in [('final', F.dataset(seeds['final'], (0, 2, 4, 6, 8, 10, 14, 18),
                                          (.75, 1.5, 3, 5), n=131072)),
                        ('channel', M.channel_cases(seeds['channel']))]:
        rows = []
        for m in models:
            rows.extend(measure(m, c) for c in cases)
            print(name, m['name'], flush=True)
        M.S.write_rows(a.output/(name+'.csv'), rows)
        M.S.write_json(a.output/(name+'_summary.json'), M.summarize(rows, advanced_guard))


if __name__ == '__main__':
    main()
