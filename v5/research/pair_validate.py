#!/usr/bin/env python3
"""Held-out validation of U85G; C5VRX by Twotoz and the contributors.

Frozen after the 252-formula screening in pair_screen.json. This is a
synthetic waveform/goggle model, not measured sensitivity or flight range.
Usage: python v5/research/pair_validate.py output.json [--quick]
"""
import argparse
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'tools'))
import gen_pair_demod as G
import sim
from range_study import threshold, usable


def candidate_lut(deviation, kp):
    # Preserve tested narrow prototype too; firmware ships WIDE only.
    if deviation <= .80:
        return G.build(kp=kp, weight=1., rotation=.4, assist_radius=2.5, quad_gain=.3)
    if deviation >= 1.60:
        return G.build(kp=kp, weight=1., rotation=.4, assist_radius=2.5)
    return G.build(kp=kp)


def job(spec):
    c = sim.make_case(spec['seed'], spec['cnr'], spec['standard'], spec['deviation'],
                      rms=spec['rms'], filter_hz=spec['filter_hz'], offset_hz=spec['offset_hz'],
                      fields=spec['fields'], echoes=spec['echoes'], fades=spec['fades'],
                      quantizer=spec.get('quantizer', 'clip'))
    rows = []
    for name, lut in [('U85Q', G.build(kp=spec['kp'])),
                      ('CANDIDATE', candidate_lut(spec['deviation'], spec['kp']))]:
        codes = sim.u85q_codes(c['raw'], lut)
        clean = sim.u85q_codes(c['clean'], lut)
        r = sim.measure(c, codes, clean)
        rows.append(dict(spec, name=name, picture=r['picture'], sinad=r['sinad']))
    return rows


def specs(quick=False):
    seeds = [161001, 161002] if quick else [161001, 161002, 161003, 161004]
    configs = [(.69, 10e6, .5), (.80, 10e6, .5), (1., 10e6, .6),
               (1.4, 12e6, .85), (1.6, 16e6, .85), (1.7, 16e6, .85), (2., 16e6, .85)]
    base = []
    def add(d, f, k, seed, cn, off=0., rms=4.2, echoes=(), fades=(), kind='awgn'):
        base.append(dict(deviation=d, filter_hz=f, kp=k, seed=seed, cnr=cn,
                         standard=('PAL', 'NTSC')[seed % 2], rms=rms, offset_hz=off,
                         fields=1 if quick else 2, echoes=echoes, fades=fades, kind=kind))
    for d, f, k in configs:
        for seed in seeds:
            for cn in [0., 1., 2., 3., 4., 5., 6., 8., 25.]: add(d, f, k, seed, cn)
    for d, f, k in [configs[0], configs[5]]:
        for seed in [171001, 171002]:
            for off in [-.5e6, .5e6]:
                for rms in [2.5, 5.5]: add(d, f, k, seed, 25., off, rms, kind='envelope')
            for cn in [4., 8., 25.]:
                for echo in [(25., .65, .8), (100., .9, 2.)]:
                    add(d, f, k, seed, cn, echoes=[echo], kind='echo')
                add(d, f, k, seed, cn, fades=[(3., 1., -30.), (9., 3., -15.)], kind='fade')
    return base


def report(rows):
    for dev in sorted({r['deviation'] for r in rows}):
        for name in ['U85Q', 'CANDIDATE']:
            rr = [r for r in rows if r['kind'] == 'awgn' and r['deviation'] == dev and r['name'] == name]
            strong = np.mean([r['sinad'] for r in rr if r['cnr'] == 25.])
            lock, use = threshold(rr), usable(rr)
            print(f'{dev:4.2f} {name:8s} lock={lock:5.2f} usable={use:5.2f} limit={max(lock,use):5.2f} strong={strong:5.2f}')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('output', type=Path)
    p.add_argument('--quick', action='store_true')
    p.add_argument('--jobs', type=int, default=5)
    a = p.parse_args()
    rows = []
    work = specs(a.quick)
    with mp.get_context('spawn').Pool(a.jobs) as pool:
        for rr in pool.imap(job, work):
            rows.extend(rr)
            a.output.write_text(json.dumps(dict(protocol=dict(seeds='independent 161xxx/171xxx',
                baseline_sha='8819b695c7a9983bda22de4ff7647b5ecd4f0601', quick=a.quick,
                firmware_policy='WIDE only; narrow CANDIDATE is rejected research'), rows=rows)))
            if len(rows) % 20 == 0: print(f'{len(rows)}/{2*len(work)} rows', flush=True)
    report(rows)
