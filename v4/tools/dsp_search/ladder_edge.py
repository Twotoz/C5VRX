#!/usr/bin/env python3
"""Quality ladder for the self-fitting range demod (C5VRX by Twotoz/contributors).

Operator idea (2026-10-09): picture quality should fall gradually with
distance instead of a cliff. FM estimation theory agrees: the optimal
tracking-loop bandwidth shrinks as C/N falls. All EDGE structures with 3
token bits share one BitScrambler program, so a ladder of them switches by
LUT rewrite only. This scores every candidate at each C/N on randomized
VTX/board cases (AutoFit from noisy fits) with one goggle score:
SINAD (detail) minus heavy penalties for lost/false sync and sync-level
error, and reports the best rung per C/N plus PAIR/RANGE32 controls.
"""
import argparse
import itertools
import json
import multiprocessing as mp
import os
from pathlib import Path
import numpy as np

CNR = (2, 4, 6, 8, 10, 13, 16, 20, 30)
ALLOC = ((4, 32, 8), (8, 16, 8), (16, 8, 8))
KP, KI, HOLD, MIX = (.7, 1., 1.4), (.1, .2, .35, .5), (3.2,), (0., .5, 1.)
OUTPUT = ('freq', 'advance', 'avg')
STATE = {}


def cases(seed, per=4):
    import waveforms as V
    import lane_profile as L
    rng = np.random.default_rng(seed); out = []
    for cnr in CNR:
        for k in range(per):
            std = ('PAL', 'NTSC')[k % 2]
            cfo = float(rng.uniform(.5e6, 1.5e6)); rms = L.scale(3) * float(rng.uniform(.85, 1.15))
            hw = dict(deviation=float(rng.uniform(.75, 1.35)), dc=complex(*rng.uniform(-.2, .2, 2)),
                      iq_gain=float(rng.uniform(.97, 1.03)), iq_phase_deg=float(rng.uniform(-2, 2)),
                      pattern=('bars', 'zoneplate', 'checker', 'texture')[int(rng.integers(4))])
            s = int(seed + 37 * cnr + k)
            c = V.make_case(std, s, cnr, rms, short=True, cfo_hz=cfo, stimulus_seed=s + 1, lane_model=L.LANE, **hw)
            n = V.make_case(std, s, cnr, rms, short=True, cfo_hz=cfo, stimulus_seed=s + 1, lane_model=L.LANE,
                            pattern=hw['pattern'])
            for key in ('raw', 'clean', 'truth', 'region'): c[key] = c[key][65536:98304]
            import engine as S
            c['calibration'] = V.M.clean_calibration(S.decode(n['clean'][65536:98304], S.F.load_reference('OVP56')),
                                                     n['truth'][65536:98304], 3000)
            c['fit'] = dict(fit_deviation=hw['deviation'] * float(rng.uniform(.95, 1.05)),
                            fit_centre_hz=cfo + float(rng.uniform(-5e4, 5e4)))
            out.append(c)
    return out


def goggle(r):
    miss = r['h_missing'] + r['v_missing']
    return float(r['sinad'] - 4. * miss - 20. * r['false_sync_per_line'] - .3 * abs(r['sync_error_ire']))


def setup(seed, bank):
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
    STATE.update(cases=cases(seed), bank=bank)


def evaluate(item):
    import overlay_fsm as O
    import edge_fsm as F
    import search_edge as E
    from video_metrics import waveform
    key, spec = item; rows = []
    for c in STATE['cases']:
        if spec['kind'] == 'edge':
            m = F.synthesize(dict(spec['params'], observation_vectors=STATE['bank'], **c['fit']))
        else:
            m = spec['model']
        y = O.decode(c['raw'], m)
        try:
            r = waveform(y, E.clamp(c, y)); rows.append((c['cnr'], goggle(r), r['sinad'], r['h_missing'] + r['v_missing']))
        except Exception:
            rows.append((c['cnr'], -60., -20., 30))
    return key, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True); ap.add_argument('--seed', type=int, default=431000)
    ap.add_argument('--workers', type=int, default=7)
    a = ap.parse_args()
    if a.output.exists(): ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    import pair_decoder as P
    root = Path(__file__).resolve().parents[2]
    opts = json.loads((root / 'tools/range_options.json').read_text())['options']
    edge = next(o for o in opts if o['label'] == 'EDGE RANGE LAB')['model']['params']
    bank = edge['observation_vectors']
    specs = {'PAIR': dict(kind='fixed', model=next(o for o in opts if o['label'] == 'PAIR RANGE LAB')['model']),
             'RANGE32': dict(kind='fixed', model=json.loads((root / 'tools/range32_model.json').read_text()))}
    base = {k: v for k, v in edge.items() if k not in ('observation_vectors', 'fit_deviation', 'fit_centre_hz')}
    for (P_, F_, T_), kp, ki, hold, mix, out in itertools.product(ALLOC, KP, KI, HOLD, MIX, OUTPUT):
        if out == 'advance' and mix: continue  # advance ignores mix
        specs[f'E{P_}x{F_}-{out}-kp{kp}-ki{ki}-m{mix}'] = dict(kind='edge', params=dict(
            base, phases=P_, frequencies=F_, token_bits=3, kp=kp, ki=ki, hold=hold, mix=mix, output=out))
    with mp.get_context('spawn').Pool(a.workers, initializer=setup, initargs=(a.seed, bank)) as pool:
        res = dict(pool.imap_unordered(evaluate, [(k, {kk: vv for kk, vv in s.items()}) for k, s in specs.items()]))
    table = {k: {str(c): float(np.mean([g for cc, g, _, _ in rows if cc == c])) for c in CNR} for k, rows in res.items()}
    sinad = {k: {str(c): float(np.mean([s for cc, _, s, _ in rows if cc == c])) for c in CNR} for k, rows in res.items()}
    miss = {k: {str(c): float(np.sum([m for cc, _, _, m in rows if cc == c])) for c in CNR} for k, rows in res.items()}
    best = {str(c): max((k for k in table if k.startswith('E')), key=lambda k: table[k][str(c)]) for c in CNR}
    out = dict(cnr=CNR, best_edge_per_cnr=best, score=table, sinad=sinad, missed=miss,
               params={k: s.get('params') for k, s in specs.items() if s['kind'] == 'edge'})
    (a.output / 'ladder.json').write_text(json.dumps(out, indent=1))
    for c in CNR:
        b = best[str(c)]
        print(f"C/N {c:2d}: best {b:32s} score {table[b][str(c)]:6.1f} sinad {sinad[b][str(c)]:5.1f} miss {miss[b][str(c)]:3.0f} | "
              f"PAIR {table['PAIR'][str(c)]:6.1f}/{sinad['PAIR'][str(c)]:5.1f}/{miss['PAIR'][str(c)]:3.0f} "
              f"RANGE32 {table['RANGE32'][str(c)]:6.1f}/{miss['RANGE32'][str(c)]:3.0f}", flush=True)


if __name__ == '__main__':
    main()
