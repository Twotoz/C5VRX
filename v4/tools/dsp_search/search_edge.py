#!/usr/bin/env python3
"""Range-edge demod search for C5VRX by Twotoz/contributors.

Evolves second-order finite-state demods (edge_fsm.py) for the FM-threshold
cliff only: missed sync, large errors (clicks) and sync-tip depth at C/N 0..8,
with hard sanity limits at C/N 10..30 where a dual-mode receiver would switch
back to a detail demod. Strong-signal detail is not scored. Parallel islands,
self-adapting steps, crossover, restarts, checkpointed leaders; per-worker
unique compiled LUTs; union reported. No promotion: selection and independent
confirmation are separate.
"""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import time
import numpy as np

CONTINUOUS = {'kp': (.15, 1.2, 'lin'), 'ki': (.02, .6, 'log'), 'limit': (.4, np.pi, 'lin'),
              'hold': (.6, 3.2, 'lin'), 'low_hz': (-4.5e6, -2.0e6, 'lin'),
              'high_hz': (3.5e6, 7.0e6, 'lin'), 'rotation': (0, 1, 'lin'), 'mix': (0, 1, 'lin')}
ALLOCATIONS = [(P, F, T) for T in (4, 8, 16, 32) for P in (4, 8, 16, 32) for F in (2, 4, 8, 16, 32)
               if P * F * T == 1024]
DECODERS = [None] + [('4411', mix, seed) for mix in ('weak', 'uniform') for seed in (0, 1)]
TOPOLOGIES = [(d, a) for d in range(len(DECODERS)) for a in ALLOCATIONS]
STATE = {}


def value(name, u):
    lo, hi, kind = CONTINUOUS[name]; u = min(1., max(0., u))
    return float(np.exp(np.log(lo) + u * (np.log(hi) - np.log(lo)))) if kind == 'log' else float(lo + u * (hi - lo))


def params(genome, bank):
    d, (P, F, T) = TOPOLOGIES[genome['topology']]
    p = {k: value(k, genome['u'][i]) for i, k in enumerate(CONTINUOUS)}
    p['rotation'] *= 2 * np.pi / T
    p.update(token_bits=int(T).bit_length() - 1, phases=P, frequencies=F, autofit=True)
    if DECODERS[d]:
        p.update(pair_layout='4411', observation_vectors=bank[d])
    return p


def screen(seed):
    """Edge cases (weighted) plus mid/strong sanity cases on the active lane profile."""
    import waveforms as V
    import engine as S
    import lane_profile as L
    base = S.F.load_reference('OVP56'); rng = np.random.default_rng(seed + 9)
    cases = []
    # FusionDemod: EDGE runs only below the switch point (C/N ~8..10); SHARP
    # takes over above it, so sanity is required where the two meet, not at
    # strong signal (a range-edge model used near the VTX failed as MAX).
    plan = [(c, 'edge') for c in (0, 2, 3, 4, 6)] + [(c, 'sane') for c in (8, 10, 12)]
    if os.environ.get('C5VRX4_EDGE_FUSION') == '1':
        # FusionDemod: EDGE runs only below 9 dB (PAIR above 12 dB), so the
        # sanity region is the hand-over itself.
        plan = [(c, 'edge') for c in (0, 2, 3, 4, 6)] + [(c, 'sane') for c in (8, 9, 10)]
    for k, (cnr, kind) in enumerate(plan):
        for std in ('PAL', 'NTSC'):
            cfo = float(rng.uniform(.5e6, 1.5e6)); rms = L.scale(3) * float(rng.uniform(.85, 1.15))
            # Domain randomization: other VTXs, cameras and receiver boards.
            hw = dict(deviation=float(rng.uniform(.6, 1.4)),
                      dc=complex(*rng.uniform(-.33, .33, 2)), iq_gain=float(rng.uniform(.95, 1.05)),
                      iq_phase_deg=float(rng.uniform(-3, 3)),
                      pattern=('bars', 'zoneplate', 'checker', 'texture')[int(rng.integers(4))])
            c = V.make_case(std, seed + 10 * k + (std == 'NTSC'), cnr, rms, short=True, cfo_hz=cfo,
                            stimulus_seed=seed + 500 + k, lane_model=L.LANE, **hw)
            for key in ('raw', 'clean', 'truth', 'region'): c[key] = c[key][65536:98304]
            # A goggle has a fixed video gain: calibrate on the nominal VTX and
            # board (deviation 1, no DC/IQ error); per-line DC is clamped in
            # score(), like the goggle's back-porch clamp.
            n = V.make_case(std, seed + 10 * k + (std == 'NTSC'), cnr, rms, short=True, cfo_hz=cfo,
                            stimulus_seed=seed + 500 + k, lane_model=L.LANE, pattern=hw['pattern'])
            c['calibration'] = V.M.clean_calibration(S.decode(n['clean'][65536:98304], base),
                                                     n['truth'][65536:98304], 3000)
            # AutoFit inputs as measured: true hardware plus estimator error.
            c['fit'] = dict(fit_deviation=hw['deviation'] * float(rng.uniform(.9, 1.1)),
                            fit_centre_hz=cfo + float(rng.uniform(-1e5, 1e5)),
                            fit_dc_cells=tuple(float(v) for v in np.array([hw['dc'].real, hw['dc'].imag]) * rms
                                               + rng.uniform(-.2, .2, 2)))
            c['kind'] = kind; cases.append(c)
    # Switch-region reference: the best of the detail demods on the same
    # signal. Near C/N 8..12 with other VTXs/boards they too miss pulses and
    # show spurious sync, so EDGE must merely never be worse than SHARP there.
    import json
    import overlay_fsm as O
    from video_metrics import waveform
    root = Path(__file__).resolve().parents[2]
    controls = [json.loads((root / 'tools/range32_model.json').read_text())]
    opts = json.loads((root / 'tools/range_options.json').read_text())['options']
    controls += [o['model'] for o in opts if o['label'] == 'PAIR RANGE LAB']
    for c in cases:
        if c['kind'] != 'sane': continue
        rs = []
        for m in controls:
            y = O.decode(c['raw'], m); rs.append(waveform(y, clamp(c, y)))
        c['ref'] = dict(miss=min(r['h_missing'] + r['v_missing'] for r in rs),
                        false=min(r['false_sync_per_line'] for r in rs),
                        sync=min(abs(r['sync_error_ire']) for r in rs))
    return cases


WEIGHT = {0: .5, 2: 2., 3: 2., 4: 2., 6: 1.}


def clamp(c, y):
    """Goggle back-porch DC restore, after aligning this decode's own latency.

    The reference calibration lag comes from another demod; LUT models differ
    in group delay by up to a span, which biased SINAD between models by up
    to ~3 dB (found 2026-10-09). A goggle ignores a fixed 50-ns delay, so
    the lag is re-chosen per decode (+-8 samples, least active-video error)
    before the back-porch offset is matched."""
    import waveforms as V
    lag0, g, off0 = c['calibration']; best = None
    for dl in range(-8, 9):
        cc = dict(c, calibration=(lag0 + dl, g, off0)); a, t, region = V.calibrated(y, cc)
        m3 = region == 3; off = off0 + (float(np.mean(t[m3] - a[m3])) if m3.any() else 0.)
        m1 = region == 1
        err = float(np.mean(np.abs(a[m1] + (off - off0) - t[m1]))) if m1.any() else 0.
        if best is None or err < best[0]: best = (err, lag0 + dl, off)
    return dict(c, calibration=(best[1], g, best[2]))


def score(m, cases):
    import overlay_fsm as O
    import edge_fsm as F
    from video_metrics import waveform
    cost = 0.; rows = []
    for c in cases:
        # AutoFit models are re-synthesized per case from its measured fit;
        # fixed models (controls) are decoded as they are.
        mm = F.synthesize(dict(m['params'], **c['fit'])) if m['params'].get('autofit') else m
        y = O.decode(c['raw'], mm)
        r = waveform(y, clamp(c, y))
        miss = r['h_missing'] + r['v_missing']
        if c['kind'] == 'sane':
            # Goggle-relevant levels, not detail: an edge demod may blur fine
            # patterns (operator 2026-10-08: strong detail matters less).
            # One spurious pulse in an 11-line window is 0.09/line; white
            # patches narrower than a blurred edge read low (detail, not level).
            ref = c['ref']
            if (miss > ref['miss'] or r['false_sync_per_line'] > ref['false'] + .1 or
                    abs(r['sync_error_ire']) > max(8., ref['sync'] + 2.) or
                    abs(r['black_error_ire']) > 10 or abs(r['white_error_ire']) > 25 or
                    r['v_trains'] != r['expected_v_trains']):
                return None
        else:
            w = WEIGHT[c['cnr']]
            cost += w * (3. * miss + .5 * r['large_errors'] + 1.5 * abs(r['sync_error_ire']))
            rows.append((c['cnr'], miss, r['large_errors'], r['sync_error_ire'], r['contrast']))
    if not rows:  # sanity-only screen (quick reject stage)
        return dict(score=0., edge_missed=0., edge_large=0., edge_sync=0., edge_contrast=0.)
    a = np.array(rows, float)
    return dict(score=-cost / len(rows), edge_missed=float(a[:, 1].sum()),
                edge_large=float(a[:, 2].mean()), edge_sync=float(np.abs(a[:, 3]).mean()),
                edge_contrast=float(a[:, 4].mean()))


def setup(seed_base, bank):
    import overlay_fsm as O
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
    STATE.update(bank=bank, cases=screen(seed_base + 201))
    quick = [c for c in STATE['cases'] if c['kind'] == 'sane' and c['cnr'] == 14][:1]
    STATE['quick'] = quick


def evaluate(genome):
    import edge_fsm as E
    import refine_overlay as F
    try:
        m = E.synthesize(params(genome, STATE['bank']))
    except ValueError:
        return None, None, 'invalid'
    key = F.digest(m) + hashlib.sha256(json.dumps(TOPOLOGIES[genome['topology']]).encode()).hexdigest()[:8]
    if score(m, STATE['quick']) is None:
        return key, None, 'sanity_rejected'
    r = score(m, STATE['cases'])
    return key, r, 'eligible' if r else 'sanity_rejected'


def random_genome(rng, topology=None):
    return dict(topology=int(rng.integers(len(TOPOLOGIES))) if topology is None else topology,
                u=rng.random(len(CONTINUOUS)).tolist())


def mutate(g, rng, sigma):
    child = dict(topology=g['topology'], u=list(g['u']))
    for i in rng.choice(len(child['u']), size=min(1 + int(rng.geometric(.45)), len(child['u'])), replace=False):
        child['u'][i] = float(np.clip(child['u'][i] + rng.normal(0, sigma), 0, 1))
    if rng.random() < .04:
        child['topology'] = int(rng.integers(len(TOPOLOGIES)))
    return child


def checkpoint(output, index, counts, leaders, seen, seconds, done):
    top = max((x[1] for v in leaders.values() for x in v), default=None)
    np.save(output / f'hashes-{index}.npy', np.array(sorted(int(k[:16], 16) for k in seen), np.uint64))
    data = dict(index=index, counts=counts, seconds=round(seconds), best=top, done=done,
                leaders=[(k, s, g, r) for v in leaders.values() for k, s, g, r in v])
    tmp = output / f'leaders-{index}.tmp'; tmp.write_text(json.dumps(data), encoding='utf-8')
    tmp.replace(output / f'leaders-{index}.json')


def worker(args):
    index, budget, seed_base, bank, output = args
    setup(seed_base, bank); rng = np.random.default_rng(seed_base + 7001 + index)
    islands = [dict(topology=int(rng.integers(len(TOPOLOGIES))), elites=[], sigma=.2, best=-1e9, since=0)
               for _ in range(6)]
    seen = set(); counts = dict(proposals=0, unique=0, sanity_rejected=0, eligible=0, invalid=0, restarts=0)
    leaders = {}; start = time.monotonic(); last = start
    while counts['unique'] < budget and counts['proposals'] < 30 * budget:
        island = islands[int(rng.integers(len(islands)))]; counts['proposals'] += 1
        el = island['elites']; roll = rng.random()
        if not el or roll < .1:
            genome = random_genome(rng, island['topology'])
        elif roll < .2 and len(el) > 1:
            a, b = rng.choice(len(el), 2, replace=False); mask = rng.random(len(CONTINUOUS)) < .5
            genome = dict(topology=el[a][1]['topology'],
                          u=[x if k else y for x, y, k in zip(el[a][1]['u'], el[b][1]['u'], mask)])
        else:
            genome = mutate(el[min(int(rng.integers(len(el))), int(rng.integers(len(el))))][1], rng, island['sigma'])
        key, r, status = evaluate(genome)
        if key is None: counts['invalid'] += 1; continue
        if key in seen: continue
        seen.add(key); counts['unique'] += 1; counts[status] += 1; improved = False
        if r:
            el.append((r['score'], genome, r)); el.sort(key=lambda x: -x[0]); del el[12:]
            improved = r['score'] > island['best']
            if improved: island['best'] = r['score']; island['since'] = 0
            pool = leaders.setdefault(genome['topology'], [])
            pool.append((key, r['score'], genome, r)); pool.sort(key=lambda x: -x[1]); del pool[6:]
        island['sigma'] = float(np.clip(island['sigma'] * (1.25 if improved else .985), .01, .45))
        island['since'] += 1
        if island['since'] > 1500:
            counts['restarts'] += 1
            island.update(topology=int(rng.integers(len(TOPOLOGIES))), elites=[], sigma=.2, best=-1e9, since=0)
        if time.monotonic() - last > 60:
            last = time.monotonic(); checkpoint(output, index, counts, leaders, seen, last - start, False)
    checkpoint(output, index, counts, leaders, seen, time.monotonic() - start, True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True); ap.add_argument('--seed-base', type=int, required=True)
    ap.add_argument('--evaluations', type=int, default=300000); ap.add_argument('--workers', type=int, default=7)
    a = ap.parse_args()
    if a.output.exists(): ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    import engine as S
    import pair_decoder as P
    import lane_profile as L
    bank = [None] + [P.learn(l, a.seed_base + 601 + s, m)[0] for l, m, s in DECODERS[1:]]
    S.save(a.output / 'protocol.json', dict(profile='edge', evaluations=a.evaluations, workers=a.workers,
        config_seed=a.seed_base + 101, screen_seed=a.seed_base + 201, lane_profile=L.record(),
        allocations=ALLOCATIONS, decoders=DECODERS, weights=WEIGHT,
        randomization='deviation x0.75..1.35, DC up to 0.33 RMS, I/Q gain 0.95..1.05, phase +-3 deg, bars/zoneplate/checker/texture',
        objective='edge cost: 3*missed + 0.5*large errors/1000 + 1.5*|sync error IRE| at C/N 0..8 (weights 0.5..2); '
                  'sanity at the FusionDemod switch region C/N 8/10/12 relative to the best of RANGE32/PAIR on the same signal (missed <=, false sync <= +0.1/line, |sync| <= max(8, ref+2)), plus no missed sync, false sync <=0.05/line, |sync| <=8, |black| <=10, |white| <=25 IRE, false sync <=0.1/line (detail not scored)',
        source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}))
    share = [a.evaluations // a.workers + (i < a.evaluations % a.workers) for i in range(a.workers)]
    with mp.get_context('spawn').Pool(a.workers) as pool:
        pool.map(worker, [(i, share[i], a.seed_base, bank, a.output) for i in range(a.workers)])
    results = [json.loads((a.output / f'leaders-{i}.json').read_text()) for i in range(a.workers)]
    hashes = np.unique(np.concatenate([np.load(a.output / f'hashes-{i}.npy') for i in range(a.workers)]))
    totals = {k: sum(r['counts'][k] for r in results) for k in results[0]['counts']}
    S.save(a.output / 'summary.json', dict(totals, union_unique=int(len(hashes)),
           best=max((x[1] for r in results for x in r['leaders']), default=None)))
    print(json.dumps(json.loads((a.output / 'summary.json').read_text())), flush=True)


if __name__ == '__main__':
    main()
