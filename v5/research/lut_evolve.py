#!/usr/bin/env python3
"""Evolve the entire shared LUT for C5VRX by Twotoz and the contributors.

Uses PR #190/#198 as seeds, not a fixed formula/phase-state interpretation.
Any legal 16-bit LUT word and any ten distinct raw pair bits are reachable.
Still scoped to the existing two-lookup hardware skeleton: not all C5 DSP.
"""
import argparse
from dataclasses import dataclass
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
import numpy as np
import demod_search as D


@dataclass
class Genome:
    token_bits: int
    bits: list
    lut: np.ndarray

    def __post_init__(self):
        if self.token_bits not in range(2, 11):
            raise ValueError('this three-instruction worker supports 2..10 token bits')
        if len(self.bits) != 10 or len(set(self.bits)) != 10 or any(b not in range(16) for b in self.bits):
            raise ValueError('need ten distinct raw pair bits')
        values = np.asarray(self.lut)
        if values.shape != (1024,) or np.any(values < 0) or np.any(values > 65535):
            raise ValueError('need exactly 1024 unsigned 16-bit shared words')
        self.lut = values.astype(np.uint16)

    @property
    def id(self):
        return hashlib.sha256(bytes([self.token_bits] + self.bits) + self.lut.astype('<u2').tobytes()).hexdigest()[:16]

    def record(self):
        return dict(id=self.id, token_bits=self.token_bits, bits=self.bits, lut=self.lut.tolist())

    @classmethod
    def restore(cls, record):
        g = cls(record['token_bits'], record['bits'], record['lut'])
        if g.id != record['id']:
            raise ValueError('genome hash mismatch')
        return g


def synthesize(g):
    # O14..O15 are unused DAC byte padding. Reuse them for up to eight
    # retained bits, using the worker's spare nop rather than another bundle.
    sb, tb = 10 - g.token_bits, g.token_bits
    text = ('# C5VRX by Twotoz and the contributors: evolved shared LUT.\n'
            '# New field wiring needs physical timing/continuity validation.\n'
            'cfg prefetch true\ncfg eof_on downstream\ncfg trailing_bytes 0\ncfg lut_width_bits 16\n'
            'lut ' + ' '.join(map(str, g.lut)) + '\n')
    for k in range(4):
        text += f'controller_{k}:\n    set 0..5 L0..L5,\n    set 8..13 L0..L5,\n    set 14..15 L12..L13,\n'
        text += ''.join(f'    set {16+j} {bit},\n' for j, bit in enumerate(g.bits))
        low = min(sb, 6)
        if low:
            text += f'    set 26..{25+low} L6..L{5+low},\n'
        text += f'    read 16,\n    write 16,\n    nop\nworker_{k}:\n    set 16..{15+tb} L{16-tb}..L15,\n'
        if sb > 6:
            text += f'    set {16+tb}..{21+tb} O26..O31,\n'
            text += f'    set {22+tb}..25 O14..O{7+sb}\n'
        elif sb:
            text += f'    set {16+tb}..25 O26..O{25+sb},\n    nop\n'
        else:
            text += '    nop\n'
    return text


def seeded(d):
    return Genome(d.token_bits, D.G.pair_bits(d.layout).tolist(), D.build(d))


def relabel(g, state_order=None, token_order=None):
    """Exact behavioral invariance while removing fixed phase/state labels.

    State zero stays startup zero. State and token meanings may then evolve
    freely; every transition/observation/output field is mutable afterwards.
    """
    nt, ns = 1 << g.token_bits, 1 << (10 - g.token_bits)
    sp = np.arange(ns) if state_order is None else np.asarray(state_order)
    tp = np.arange(nt) if token_order is None else np.asarray(token_order)
    if set(sp.tolist()) != set(range(ns)) or sp[0] != 0 or set(tp.tolist()) != set(range(nt)):
        raise ValueError('permutations must be bijective and preserve startup state zero')
    encoder = tp[g.lut >> (16 - g.token_bits)]
    low_mask = (1 << (16 - g.token_bits)) - 1
    old = g.lut & low_mask
    old_state = old >> 6
    transformed = (old & 63) | (sp[old_state] << 6)
    low = np.empty(1024, dtype=np.uint16)
    old_addresses = np.arange(1024)
    new_addresses = (sp[old_addresses >> g.token_bits] << g.token_bits) | tp[old_addresses & (nt - 1)]
    low[new_addresses] = transformed
    return Genome(g.token_bits, list(g.bits), low | (encoder << (16 - g.token_bits)))


def mutate(g, rng):
    """Sparse local moves AND unrestricted redraws; no analytic loop law.

    Changes to low/high fields remain independent despite shared addresses.
    There is no restriction to angular sectors, gains, or phase-like states.
    """
    child = Genome(g.token_bits, list(g.bits), g.lut.copy())
    nt, ns = 1 << g.token_bits, 1 << (10 - g.token_bits)
    if rng.random() < .15:
        states = np.r_[0, rng.permutation(np.arange(1, ns))]
        child = relabel(child, states, rng.permutation(nt))
    count = int(rng.choice([1, 2, 4, 8, 16, 64]))
    idx = rng.choice(1024, size=count, replace=False)
    mode = int(rng.integers(5))
    words = child.lut[idx].astype(np.int64)
    if mode == 0:
        # Small DAC moves can retain quality while full redraws ensure reach.
        if rng.random() < .8:
            code = np.clip((words & 63) + rng.choice([-3, -2, -1, 1, 2, 3], size=count), 0, 63)
        else:
            code = rng.integers(64, size=count)
        child.lut[idx] = (words & 0xffc0) | code
    elif mode == 1:
        mask = (ns - 1) << 6
        child.lut[idx] = (words & (65535 ^ mask)) | (rng.integers(ns, size=count) << 6)
    elif mode == 2:
        mask = (nt - 1) << (16 - g.token_bits)
        child.lut[idx] = (words & (65535 ^ mask)) | (rng.integers(nt, size=count) << (16 - g.token_bits))
    elif mode == 3:
        child.lut[idx] = rng.integers(65536, size=count, dtype=np.uint16)
    else:
        # All C(16,10)=8008 input-bit subsets are reachable, not four layouts.
        j = int(rng.integers(10))
        choices = [b for b in range(16) if b not in child.bits]
        child.bits[j] = int(rng.choice(choices))
    return child


def initial_population(rng, size):
    controls = D.population(210011, max(size, 6))[:size]
    genomes = [seeded(d) for d in controls]
    # A fully random state machine is a poor seed, but tests the unrestricted
    # representation and prevents analytic seeds being the only start points.
    for tb in range(2, 11):
        genomes.append(phase_seed(tb))
        genomes.append(Genome(tb, rng.choice(16, 10, replace=False).tolist(),
                              rng.integers(65536, size=1024, dtype=np.uint16)))
    return genomes


def phase_seed(tb):
    """Bootstrap every width; no phase interpretation is required thereafter."""
    nt, ns = 1 << tb, 1 << (10-tb)
    za, _ = D.G.vectors('4411')
    token = np.floor((np.angle(za)+np.pi)*nt/(2*np.pi)).astype(int) % nt
    phase = (np.arange(ns)+.5)*2*np.pi/ns-np.pi
    obs = (np.arange(nt)+.5)*2*np.pi/nt-np.pi
    error = D.wrap(obs[None,:]-phase[:,None])
    nxt = np.floor((D.wrap(phase[:,None]+.85*error)+np.pi)*ns/(2*np.pi)).astype(int)%ns
    hz = D.wrap(phase[nxt]-phase[:,None])/(2*np.pi*50e-9)
    code = np.rint(np.clip((hz+6e6)/15e6*63,0,63)).astype(int)
    return Genome(tb,D.G.pair_bits('4411').tolist(),
                  (code|(nxt<<6)).ravel()|(token<<(16-tb)))


def evaluate(g, cases):
    rows = []
    for spec, c, baseline in cases:
        codes = D.sim._pair_codes(c['raw'], g.lut, np.array(g.bits, dtype=np.int64), g.token_bits)
        clean = D.sim._pair_codes(c['clean'], g.lut, np.array(g.bits, dtype=np.int64), g.token_bits)
        result = D.sim.measure(c, codes, clean)
        rows.append(dict(spec, baseline=baseline, picture=result['picture'], sinad=result['sinad']))
    return dict(genome=g.record(), assessment=D.assess(rows), rows=rows)


def case_specs(split, seed_offset=900000):
    # Fresh seeds: #198's earlier final rows have already been inspected.
    return [dict(spec, seed=spec['seed'] + seed_offset) for spec in D.case_specs(split)]


def load_cases(split, crop, seed_offset=900000):
    cases = []
    for spec in case_specs(split, seed_offset):
        c = D.sim.make_case(spec['seed'], spec['cnr'], spec['standard'], spec['deviation'],
                            filter_hz=16e6, rms=spec['rms'], offset_hz=spec['offset_hz'],
                            echoes=spec['echoes'], fades=spec['fades'], quantizer='ultrafine',
                            fields=1 if split == 'screen' else 2)
        if split == 'screen' and crop:
            for field in ('raw', 'clean', 'truth', 'region'):
                c[field] = c[field][:crop]
        baseline = D.sim.measure(c)
        cases.append((spec, c, dict(picture=baseline['picture'], sinad=baseline['sinad'])))
    return cases


def key(record):
    a = record['assessment']
    return a['eligible'], -len(a['violations']), a['score']


def parents(archive, count):
    """Elites plus width niches; a coarse architecture is not erased at gen 1."""
    rank = sorted(archive, key=key, reverse=True)
    selected = []
    for tb in sorted({r['genome']['token_bits'] for r in archive}):
        niche = next(r for r in rank if r['genome']['token_bits'] == tb)
        selected.append(niche)
    for r in rank:
        if len(selected) >= count:
            break
        if r['genome']['id'] not in {x['genome']['id'] for x in selected}:
            selected.append(r)
    return selected


_CASES = None


def init_worker(cases):
    global _CASES
    _CASES = cases


def worker(g):
    return evaluate(g, _CASES)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('output', type=Path)
    p.add_argument('--population', type=int, default=32)
    p.add_argument('--generations', type=int, default=32, help='offspring generations, no claim of optimum')
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--seed', type=int, default=240011)
    p.add_argument('--finalists', type=int, default=3)
    p.add_argument('--case-seed-offset', type=int, default=900000, help='fresh seeds for each new study')
    p.add_argument('--fast-training', action='store_true', help='old short-crop diagnostic; full training is default')
    p.add_argument('--resume', action='store_true', help='continue from a complete generation checkpoint')
    a = p.parse_args()
    if a.population < 18 or a.generations < 1 or a.jobs < 1 or a.finalists < 1:
        p.error('population >=18 and positive generations/jobs/finalists required')
    source_hashes = {name: hashlib.sha256((D.HERE / name).read_bytes()).hexdigest()
                     for name in ['lut_evolve.py', 'demod_search.py', 'sim.py']}
    config = dict(seed=a.seed, population=a.population, finalists=a.finalists, source_hashes=source_hashes,
                  case_seed_offset=a.case_seed_offset, fast_training=a.fast_training,
                  baseline_lut_sha256=hashlib.sha256(D.sim.u85q_lut().astype('<u2').tobytes()).hexdigest(),
                  generator_sha256=hashlib.sha256((D.HERE.parent / 'tools' / 'gen_pair_demod.py').read_bytes()).hexdigest(),
                  evidence='synthetic WIDE only; existing two-lookup skeleton; not a C5-wide optimum')
    rng = np.random.default_rng(a.seed)
    archive, start = [], 0
    if a.resume:
        saved = json.loads((a.output / 'checkpoint.json').read_text())
        if saved['config'] != config:
            p.error('resume requires identical seed, population, finalists, baseline and source hashes')
        if (a.output / 'frozen.json').exists():
            p.error('finalists already frozen: use a new experiment and new holdout seeds for further tuning')
        archive, start = saved['archive'], saved['generation'] + 1
        rng.bit_generator.state = saved['rng_state']
    else:
        a.output.mkdir(parents=True, exist_ok=False)
        D.save(a.output / 'protocol.json', dict(config, generations=a.generations,
               input_subsets=8008, genome='all 16384 shared LUT bits + ten input bits + token/state width',
               screen_specs=case_specs('screen',a.case_seed_offset), validation_specs=case_specs('validation',a.case_seed_offset),
               training_indices=[0,2,3,4,5,6] if a.fast_training else list(range(9)),
               training_samples=220000 if a.fast_training else 'full field'))
    print('Loading folding-model training cases', flush=True)
    full = load_cases('screen', 0, a.case_seed_offset)
    training = full
    if a.fast_training:
        training = []
        for index in [0,2,3,4,5,6]:
            spec,c,_ = full[index]
            short = dict(c)
            for field in ('raw','clean','truth','region'):
                short[field]=c[field][:220000]
            baseline=D.sim.measure(short)
            training.append((spec,short,dict(picture=baseline['picture'],sinad=baseline['sinad'])))
    with mp.get_context('spawn').Pool(a.jobs, initializer=init_worker, initargs=(training,)) as pool:
        for generation in range(start, a.generations + 1):
            if generation == 0:
                batch = initial_population(rng, a.population)
            else:
                elites = parents(archive, max(9, a.population // 2))
                seen = {r['genome']['id'] for r in archive}
                batch = []
                while len(batch) < a.population:
                    if rng.random() < .1:
                        tb = int(rng.choice(np.arange(2,11)))
                        g = Genome(tb, rng.choice(16, 10, replace=False).tolist(),
                                   rng.integers(65536, size=1024, dtype=np.uint16))
                    else:
                        parent = elites[int(rng.integers(len(elites)))]['genome']
                        g = mutate(Genome.restore(parent), rng)
                    if g.id not in seen:
                        seen.add(g.id)
                        batch.append(g)
            for r in pool.imap(worker, batch):
                archive.append(dict(r, generation=generation))
            D.save(a.output / 'checkpoint.json', dict(config=config, generation=generation,
                   rng_state=rng.bit_generator.state, archive=archive))
            best = max(archive, key=key)
            print(f"generation {generation}/{a.generations} evaluated={len(archive)} best={best['genome']['id']} weak_gain={best['assessment'].get('weak_mean_gain_db', 0):+.3f}", flush=True)
    # A broader screen rejects fast-crop artifacts before final seeds exist.
    shortlist = sorted(archive, key=key, reverse=True)[:max(6, a.finalists * 2)]
    screened = [evaluate(Genome.restore(r['genome']), full) for r in shortlist]
    D.save(a.output / 'screen.json', screened)
    baseline_id = seeded(D.Design()).id
    finalists = [r for r in sorted(screened, key=key, reverse=True) if r['genome']['id'] != baseline_id][:a.finalists]
    D.save(a.output / 'frozen.json', [r['genome'] for r in finalists])
    print('Frozen finalists; loading independent full fields', flush=True)
    validation = load_cases('validation', 0, a.case_seed_offset)
    checked = []
    for r in finalists:
        g = Genome.restore(r['genome'])
        print(f'Validating {g.id}', flush=True)
        result = evaluate(g, validation)
        checked.append(dict(result, screen_eligible=r['assessment']['eligible']))
        D.save(a.output / 'validation.json', checked)
    passed = [r for r in sorted(checked, key=key, reverse=True)
              if r['screen_eligible'] and r['assessment']['eligible']]
    decision = dict(status='synthetic_pass_hardware_pending' if passed else 'no_candidate_passes',
                    winner=passed[0]['genome']['id'] if passed else None,
                    evaluated=len(archive), generations=a.generations, hardware_tested=False)
    D.save(a.output / 'decision.json', decision)
    if passed:
        (a.output / 'candidate.bsasm').write_text(synthesize(Genome.restore(passed[0]['genome'])))
    print(json.dumps(decision), flush=True)


if __name__ == '__main__':
    main()
