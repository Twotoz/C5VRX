#!/usr/bin/env python3
"""Reproduce analytic screening; C5VRX by Twotoz and the contributors.

252 formulas, fifteen fixed AWGN raster chunks of 110,000 IQ bytes each.
The screening metric is luma SINAD ONLY: it cannot establish better range.
Use pair_validate.py for goggle lock and independent full-field validation.
No LUT entry is fitted; selection only chooses analytic formula parameters.
"""
import itertools
import json
from pathlib import Path
import sys
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'tools'))
import gen_pair_demod as G
import sim


def candidates():
    for layout, weight, rotation, quad, centroid in itertools.product(
            ['4411', '3322'], [0., .5, 1., 1.5, 2., 3.], [0., .12, .24, .4], [True, False], [False, True]):
        if layout == '3322' and (quad or weight == 0): continue
        if weight == 0 and rotation != 0: continue
        yield dict(layout=layout, weight=weight, rotation=rotation, quad=quad, centroid=centroid)
    for output, weight, rotation, quad, centroid in itertools.product(
            ['innovation', 'update'], [0., .5, 1.], [0., .24, .4], [True, False], [False, True]):
        if weight == 0 and rotation != 0: continue
        yield dict(layout='4411', weight=weight, rotation=rotation, quad=quad, centroid=centroid, output=output)
    for weight, rotation, radius, gain in itertools.product([.5, 1., 2., 3.], [0., .4], [1.6, 2.5, 3.5], [.3, .5, .7]):
        yield dict(layout='4411', weight=weight, rotation=rotation, quad=True, centroid=False,
                   output='advance', assist_radius=radius, quad_gain=gain)


def main(output):
    cases = []
    for dev, f, kp in [(.69, 10e6, .5), (1., 10e6, .6), (1.4, 12e6, .85), (1.7, 16e6, .85), (2., 16e6, .85)]:
        for cn in [2., 6., 25.]:
            c = sim.make_case(11001 + int(dev * 100), cn, 'PAL', dev, filter_hz=f, fields=1)
            for key in ['raw', 'clean', 'truth', 'region']: c[key] = c[key][:110000]
            cases.append((dev, cn, kp, c))
    results = []
    for params in candidates():
        values = []
        for dev, cn, kp, c in cases:
            lut = G.build(kp=kp, **params)
            codes = sim._pair_codes(c['raw'], lut, G.pair_bits(params['layout']), 5)
            clean = sim._pair_codes(c['clean'], lut, G.pair_bits(params['layout']), 5)
            y = sim.apply_cal(sim.video_out(codes), sim.calibrate(sim.video_out(clean), c['truth'], c['region']))
            mask = c['region'][3000:-3000] == 1
            truth = c['truth'][3000:-3000][mask]
            error = y[3000:-3000][mask] - truth
            values.append(float(10 * np.log10(np.var(truth) / np.mean(error * error))))
        score = float(np.mean([v for i, v in enumerate(values) if i % 3 < 2]))
        results.append(dict(params=params, score=score, values=values))
        if len(results) % 20 == 0: print(len(results), flush=True)
    output.write_text(json.dumps(sorted(results, key=lambda x: -x['score']), indent=2))


if __name__ == '__main__':
    main(Path(sys.argv[1]))
