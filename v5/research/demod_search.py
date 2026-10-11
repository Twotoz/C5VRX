#!/usr/bin/env python3
"""Constrained demod architecture search for C5VRX by Twotoz/contributors.

Extends PR #190's analytic shared-LUT tracker and PR #55's predictive idea.
Offline synthesis/evaluation only: no CPU sample loop or firmware switching.
Synthetic pass != RF range proof. Run with --help for reproducible budgets.
"""
import argparse
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'tools'))
import gen_pair_demod as G
import sim


@dataclass(frozen=True)
class Design:
    family: str = 'tracker'
    token_bits: int = 5
    phase_bits: int = 5
    layout: str = '4411'
    kp: float = .85
    ki: float = .25
    origin_gain: float = .5
    origin_radius: float = .8
    weight: float = 0.
    assist_radius: float = 2.5
    rotation: float = .4
    output: str = 'advance'


def wrap(x):
    return (x + np.pi) % (2 * np.pi) - np.pi


def geometry(d):
    """Explicit storage gate: six DAC bits, <= six retained O26..O31 bits.

    Stage-1 token and stage-2 output occupy disjoint fields of ONE LUT word.
    A smaller token permits phase+frequency history at reduced precision.
    """
    state_bits = 10 - d.token_bits
    frequency_bits = state_bits - d.phase_bits
    if not (4 <= d.token_bits <= 6 and 3 <= d.phase_bits <= state_bits <= 6):
        raise ValueError('unsupported token/state width or output-register capacity')
    if d.family not in ('tracker', 'predictive'):
        raise ValueError('unknown family')
    if (d.family == 'tracker') != (frequency_bits == 0):
        raise ValueError('tracker has phase only; predictive needs frequency bits')
    if d.output not in ('advance', 'frequency') or (d.family == 'tracker' and d.output != 'advance'):
        raise ValueError('unsupported output law')
    if not (0. < d.kp <= 1. and 0. < d.ki <= 1. and 0. <= d.origin_gain <= 1.):
        raise ValueError('invalid loop gains')
    if d.origin_radius < 0 or d.assist_radius <= 0 or d.weight < 0:
        raise ValueError('invalid radius/weight')
    G.pair_bits(d.layout)
    return state_bits, frequency_bits


def build(d):
    state_bits, frequency_bits = geometry(d)
    # The explicit control must reproduce the inherited tracker exactly.
    if d.family == 'tracker' and d.token_bits == 5 and d.origin_radius == .8:
        return G.build(kp=d.kp, weight=d.weight, rotation=d.rotation,
                       layout=d.layout, assist_radius=d.assist_radius,
                       quad_gain=d.origin_gain)
    nt, np_ = 1 << d.token_bits, 1 << d.phase_bits
    za, zb = G.vectors(d.layout)
    weight = d.weight * np.maximum(0., 1. - abs(za) ** 2 / d.assist_radius ** 2)
    z = za + weight * zb / abs(zb) * np.exp(-1j * d.rotation)
    angle = np.angle(z)
    nq = 4 if d.origin_radius else 0
    ns = nt - nq
    token = np.floor((angle + np.pi) * ns / (2 * np.pi)).astype(int) % ns
    low = abs(za) < d.origin_radius
    if nq:
        token[low] = ns + np.floor((angle[low] + np.pi) / (np.pi / 2)).astype(int) % 4
    observation = (np.arange(nt) + .5) * 2 * np.pi / ns - np.pi
    if nq:
        observation[ns:] = (np.arange(4) + .5) * np.pi / 2 - np.pi
    state = np.arange(1 << state_bits)
    phase = ((state & (np_ - 1)) + .5) * 2 * np.pi / np_ - np.pi
    gains = np.full(nt, d.kp)
    if nq:
        gains[ns:] = d.origin_gain
    if frequency_bits:
        nf = 1 << frequency_bits
        bins = np.linspace(-6e6, 9e6, nf)
        frequency = bins[state >> d.phase_bits]
        predicted = phase + 2 * np.pi * 50e-9 * frequency
    else:
        frequency = np.zeros(len(state))
        predicted = phase
    error = wrap(observation[None, :] - predicted[:, None])
    new_phase = np.floor((wrap(predicted[:, None] + gains * error) + np.pi)
                         * np_ / (2 * np.pi)).astype(int) % np_
    advance_hz = wrap((new_phase + .5) * 2 * np.pi / np_ - np.pi
                      - phase[:, None]) / (2 * np.pi * 50e-9)
    next_state = new_phase
    if frequency_bits:
        confidence = np.ones(nt)
        if nq:
            confidence[ns:] = d.origin_gain / d.kp
        continuous_frequency = frequency[:, None] + d.ki * confidence * error / (2 * np.pi * 50e-9)
        fi = np.rint(np.clip((continuous_frequency + 6e6) / 15e6, 0, 1) * (nf - 1)).astype(int)
        next_state = next_state | (fi << d.phase_bits)
        if d.output == 'frequency':
            # Emit the continuous update; quantize only the retained history.
            advance_hz = continuous_frequency
    code = np.rint(np.clip((advance_hz + 6e6) / 15e6 * 63, 0, 63)).astype(int)
    lut = ((code | (next_state << 6)).ravel() | (token << (16 - d.token_bits))).astype(np.uint16)
    if lut.shape != (1024,):
        raise AssertionError('one 1024x16 resident LUT required')
    return lut


def program(d, lut=None):
    """Same eight-block/two-lookup skeleton; variable token/state wiring.

    Assembly acceptance proves syntax, not physical timing for new geometry.
    """
    state_bits, _ = geometry(d)
    lut = build(d) if lut is None else np.asarray(lut, dtype=np.uint16)
    if lut.shape != (1024,):
        raise ValueError('one 1024-word table required')
    text = G.program(lut, d.layout)
    text = text.replace('analytic pair-assisted tracker.', 'offline search candidate; hardware timing unproven.')
    text = text.replace('set 26..31 L6..L11,', f'set 26..{25 + state_bits} L6..L{5 + state_bits},')
    text = text.replace('set 16..20 L11..L15,',
                        f'set 16..{15 + d.token_bits} L{16 - d.token_bits}..L15,')
    text = text.replace('set 21..25 O26..O30,',
                        f'set {16 + d.token_bits}..25 O26..O{25 + state_bits},')
    return text


def design_id(d):
    return hashlib.sha256(json.dumps(asdict(d), sort_keys=True).encode()).hexdigest()[:12]


def population(seed, budget):
    """Deterministic mixed-family seeds + reproducible random exploration.

    This is a bounded search, never a claim of global optimality. Keeping
    controls in the archive makes regressions and model mistakes observable.
    """
    if budget < 6:
        raise ValueError('budget must include at least six controls/architectures')
    base = Design()
    seeds = [base, replace(base, weight=1.),
             replace(base, token_bits=4, phase_bits=6),
             replace(base, token_bits=6, phase_bits=4),
             replace(base, family='predictive', token_bits=4, phase_bits=4),
             replace(base, family='predictive', token_bits=5, phase_bits=4)]
    architectures = [('tracker', 5, 5), ('tracker', 4, 6), ('tracker', 6, 4),
                     ('predictive', 4, 4), ('predictive', 4, 3), ('predictive', 5, 4),
                     ('predictive', 5, 3)]
    rng = np.random.default_rng(seed)
    unique = {design_id(d): d for d in seeds}
    while len(unique) < budget:
        family, tb, pb = architectures[int(rng.integers(len(architectures)))]
        d = Design(family=family, token_bits=tb, phase_bits=pb,
                   layout=str(rng.choice(['4411', '3322', '3331', '3313'])),
                   kp=float(rng.choice([.45, .6, .7, .85, 1.])),
                   ki=float(rng.choice([.15, .3, .5, .75, 1.])),
                   origin_gain=float(rng.choice([.25, .5, .75, 1.])),
                   origin_radius=float(rng.choice([0., .8, 1.6, 2.5])),
                   weight=float(rng.choice([0., .5, 1., 2.])),
                   assist_radius=float(rng.choice([1.6, 2.5, 3.5])),
                   rotation=float(rng.choice([0., .2, .4])),
                   output='advance' if family == 'tracker' else str(rng.choice(['advance', 'frequency'])))
        geometry(d)
        unique[design_id(d)] = d
    return list(unique.values())


def case_specs(split):
    """Screen and final tests use disjoint raster/noise seeds and full fields.

    Search targets WIDE only. Other firmware profiles are outside this study.
    Echo conditions include the known U85G failure; independent seeds, not
    unseen propagation physics. Fixed radius/gain is a simulator limitation.
    """
    cases = []
    def add(dev, cn, seed, kind='awgn', **kw):
        cases.append(dict(deviation=dev, cnr=cn, seed=seed,
                          standard=('PAL', 'NTSC')[seed % 2], kind=kind,
                          rms=kw.pop('rms', 4.2), offset_hz=kw.pop('offset_hz', 0.),
                          echoes=kw.pop('echoes', []), fades=kw.pop('fades', []), **kw))
    if split == 'screen':
        for dev in [1.6, 1.7, 2.]:
            add(dev, 4., 211001 + int(dev * 100))
        for seed in [212001, 212002]:
            add(1.7, 4., seed, 'echo', echoes=[(25., .65, .8)])
        for seed in [213001, 213002]:
            add(1.7, 25., seed, 'strong')
        add(1.7, 25., 214001, 'envelope', rms=5.5, offset_hz=.5e6)
        add(1.7, 6., 215002, 'fade', fades=[(1., 1., -25.)])
    elif split == 'validation':
        for dev in [1.6, 1.7, 2.]:
            for seed in [221001, 221002]:
                for cn in [2., 4., 6., 25.]:
                    add(dev, cn, seed)
        for seed in [222001, 222002]:
            for cn in [4., 8., 25.]:
                for echo in [(25., .65, .8), (100., .9, 2.), (50., .8, 1.4)]:
                    add(1.7, cn, seed, 'echo', echoes=[echo])
                add(1.7, cn, seed, 'fade', fades=[(3., 1., -30.), (9., 3., -15.)])
        for seed in [223001, 223002]:
            for rms in [2.5, 5.5]:
                for offset in [-.5e6, .5e6]:
                    add(1.7, 25., seed, 'envelope', rms=rms, offset_hz=offset)
    else:
        raise ValueError('unknown split')
    return cases


def load_cases(split, crop):
    cases = []
    for spec in case_specs(split):
        c = sim.make_case(spec['seed'], spec['cnr'], spec['standard'], spec['deviation'],
                          filter_hz=16e6, rms=spec['rms'], offset_hz=spec['offset_hz'],
                          echoes=spec['echoes'], fades=spec['fades'],
                          fields=1 if split == 'screen' else 2, quantizer='ultrafine')
        if split == 'screen':
            for key in ('raw', 'clean', 'truth', 'region'):
                c[key] = c[key][:crop]
        r = sim.measure(c)
        cases.append((spec, c, dict(picture=r['picture'], sinad=r['sinad'])))
    return cases


def evaluate(d, cases):
    lut = build(d)
    rows = []
    for spec, c, baseline in cases:
        codes = sim._pair_codes(c['raw'], lut, G.pair_bits(d.layout), d.token_bits)
        clean = sim._pair_codes(c['clean'], lut, G.pair_bits(d.layout), d.token_bits)
        r = sim.measure(c, codes, clean)
        rows.append(dict(spec, baseline=baseline, picture=r['picture'], sinad=r['sinad']))
    return rows


def assess(rows):
    """Per-case gates precede ranking: mean gain cannot hide a lost picture.

    Limits are engineering choices for this experiment, not measured visual
    equivalence. Any nonfinite result is rejected. Separate true RF gates in
    docs/DEMOD_SEARCH.md remain mandatory even after a synthetic pass.
    """
    if not rows or any(not np.isfinite(v) for r in rows for v in
                       [r['picture'], r['sinad'], r['baseline']['picture'], r['baseline']['sinad']]):
        return dict(eligible=False, violations=['nonfinite/empty metrics'], score=-1e9)
    dp = np.array([r['picture'] - r['baseline']['picture'] for r in rows])
    ds = np.array([r['sinad'] - r['baseline']['sinad'] for r in rows])
    strong = np.array([r['cnr'] >= 25 for r in rows])
    weak = ~strong
    gain = float(np.mean(ds[weak])) if weak.any() else 0.
    violations = []
    for i, r in enumerate(rows):
        if r['baseline']['picture'] >= .5 and dp[i] < -.03:
            violations.append(f'case {i}: displayed lines {dp[i]:+.4f}')
        if r['baseline']['picture'] >= .9 and r['picture'] < .9:
            violations.append(f'case {i}: loses 90% lock')
        if ds[i] < -.5:
            violations.append(f'case {i}: SINAD {ds[i]:+.3f} dB')
    if gain < .2:
        violations.append(f'weak mean gain {gain:+.3f} dB below +0.2 dB gate')
    return dict(eligible=not violations, violations=violations,
                weak_mean_gain_db=gain, worst_sinad_delta_db=float(ds.min()),
                worst_picture_delta=float(dp.min()),
                strong_mean_delta_db=float(np.mean(ds[strong])) if strong.any() else None,
                score=gain + float(ds.min()) + 8 * float(dp.min()))


_CASES = None


def init_worker(cases):
    global _CASES
    _CASES = cases


def worker(d):
    rows = evaluate(d, _CASES)
    return dict(id=design_id(d), design=asdict(d), assessment=assess(rows), rows=rows)


def save(path, data):
    # Atomic checkpoint: an interrupted experiment never leaves half a JSON.
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def ranked(results):
    return sorted(results, key=lambda r: (r['assessment']['eligible'],
                                          -len(r['assessment']['violations']),
                                          r['assessment']['score']), reverse=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('output', type=Path, help='new experiment directory; never overwritten')
    p.add_argument('--budget', type=int, default=96)
    p.add_argument('--seed', type=int, default=210011)
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--finalists', type=int, default=3)
    p.add_argument('--screen-samples', type=int, default=300000)
    p.add_argument('--screen-only', action='store_true', help='diagnostic only; cannot export winner')
    a = p.parse_args()
    if a.jobs < 1 or a.finalists < 1 or a.screen_samples < 220000:
        p.error('positive worker/finalist count and >=220000 screening samples required')
    designs = population(a.seed, a.budget)
    a.output.mkdir(parents=True, exist_ok=False)
    protocol = dict(seed=a.seed, budget=a.budget, finalists=a.finalists,
                    screen_samples=a.screen_samples, quantizer='ultrafine bits 9,6,5,4',
                    scope='WIDE fixed 16 MHz filter; other receive profiles not studied',
                    baseline_lut_sha256=hashlib.sha256(sim.u85q_lut().astype('<u2').tobytes()).hexdigest(),
                    screen_specs=case_specs('screen'), validation_specs=case_specs('validation'),
                    source_sha256={name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                                   for name in ['demod_search.py', 'sim.py']},
                    generator_sha256=hashlib.sha256((HERE.parent / 'tools' / 'gen_pair_demod.py').read_bytes()).hexdigest(),
                    gates=dict(max_picture_loss=.03, min_lock=.9, max_sinad_loss_db=.5,
                               min_weak_mean_gain_db=.2),
                    evidence='synthetic only; no firmware promotion or RF range claim')
    save(a.output / 'protocol.json', protocol)
    print('Generating folding-model screening cases', flush=True)
    screen = load_cases('screen', a.screen_samples)
    results = []
    with mp.get_context('spawn').Pool(a.jobs, initializer=init_worker, initargs=(screen,)) as pool:
        for result in pool.imap(worker, designs):
            results.append(result)
            save(a.output / 'screen.json', results)
            if len(results) % 8 == 0 or len(results) == len(designs):
                best = ranked(results)[0]
                print(f"screen {len(results)}/{len(designs)} eligible={sum(r['assessment']['eligible'] for r in results)} best={best['id']}", flush=True)
    # Freeze before loading final seeds. No retuning/iteration on held-out rows.
    finalists = [r for r in ranked(results) if r['design'] != asdict(Design())][:a.finalists]
    save(a.output / 'frozen.json', [dict(id=r['id'], design=r['design']) for r in finalists])
    if a.screen_only:
        print('Screen only: no winner decision', flush=True)
        return
    print('Generating independent full-field validation cases', flush=True)
    validation = load_cases('validation', 0)
    checked = []
    for frozen in finalists:
        d = Design(**frozen['design'])
        print(f"Validating frozen {frozen['id']}", flush=True)
        rows = evaluate(d, validation)
        checked.append(dict(frozen, screen_eligible=next(r['assessment']['eligible'] for r in results if r['id'] == frozen['id']),
                            assessment=assess(rows), rows=rows))
        save(a.output / 'validation.json', checked)
    # Both gates required. Diagnostic finalists may be rejected in screening.
    passed = [r for r in ranked(checked) if r['screen_eligible'] and r['assessment']['eligible']]
    decision = dict(status='synthetic_pass_hardware_pending' if passed else 'no_candidate_passes',
                    winner=passed[0]['id'] if passed else None,
                    screened=len(results), validated=len(checked), hardware_tested=False)
    save(a.output / 'decision.json', decision)
    if passed:
        d = Design(**passed[0]['design'])
        (a.output / 'candidate.bsasm').write_text(program(d))
    print(json.dumps(decision), flush=True)


if __name__ == '__main__':
    main()
