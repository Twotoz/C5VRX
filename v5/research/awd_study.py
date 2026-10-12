#!/usr/bin/env python3
"""Frozen-design holdout for the AWD6 demodulator (C5VRX by Twotoz and the
C5VRX contributors).

Evaluates the checked-in `main/awd6.bsasm` through the BitScrambler host
emulator (`tools/bs_emu.py`) -- the program and LUT that would be flashed,
not a formula -- against the U85Q variant the firmware profile would use
for the same VTX deviation. Two independent splits with seeds never used
while designing AWD6:

  validation  the 56-case WIDE matrix of docs/DEMOD_SEARCH.md (PAL/NTSC,
              C/N 2..25, deviations 1.6/1.7/2.0, echoes, fades, gain
              radius and +/-0.5 MHz offsets), fresh seed offset;
  matrix      every firmware profile (deviation 0.69..2.0) x the
              situations.py field stresses (offsets +/-0.5/1.5 MHz, echo,
              mid-record deep fades, DC, I/Q imbalance, CW spur) x C/N
              4/8/25 + strong, two fields, actual ultrafine folding;
  content     flat white/black bars and an all-white second field on
              wide/default deviations with +/-1.5 MHz AFC error: the
              random-texture rasters never hold the extremes for long.

The gates are those of demod_search.assess (per case: <=3 pp displayed
lines lost where U85Q shows >=50 %, no drop below 90 % lock where U85Q
holds it, <=0.5 dB luma SINAD loss; >= +0.2 dB weak mean gain). Results
are synthetic luma/sync numbers, not measured RF sensitivity or range.

    python v5/research/awd_study.py v5/research/data/awd6_holdout_20261012c
"""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
import numpy as np
from scipy import signal as sg

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'tools'))
import bs_emu  # noqa: E402
import demod_search as D  # noqa: E402
import sim  # noqa: E402
import situations as S  # noqa: E402

PROGRAM = ROOT / 'main' / 'awd6.bsasm'
VALIDATION_SEED_OFFSET = 5000000          # unused by #198 and by every AWD6 design or earlier holdout
MATRIX_SEEDS = (101, 102)                 # design: 31, 41..44, 91..94; superseded holdouts: 61/62, 71/72
CONTENT_SEED = 111
SITUATIONS = dict(S.SITUATIONS)
# Fades start mid-record: a fade at t=0 only measures the goggle model's
# first-candidate start-up, not the loss and re-acquisition of lock.
SITUATIONS['fade3ms'] = dict(fade=(3e-3, 40e-3, -30., 6e-3))
SITUATIONS['fade1ms'] = dict(fade=(1e-3, 7e-3, -30., 4e-3))


def awd6(raw, text=None):
    return bs_emu.run(text or PROGRAM.read_text(), raw) & 63


def bars(ire, region, standard):
    """Picture area: 40-line white/black bars in field 1, white in field 2."""
    line = 2560 if standard == 'PAL' else 2542
    out = ire.copy()
    pic = region == 1
    k = np.arange(len(ire)) // line
    field2 = np.arange(len(ire)) >= len(ire) // 2
    out[pic] = np.where(field2[pic] | ((k[pic] // 40) % 2 == 0), 100., 0.)
    return out


def make_case(seed, cnr, standard, dev, filter_hz, offset_hz=0., echo=None, fade=None, dc=0., iq=None,
              spur=None, rms=4.2, fields=2, content=False):
    """situations.make_case with the actual ultrafine folding quantizer."""
    rng = np.random.default_rng(seed)
    ire, region = sim.raster(standard, seed + 7, fields)
    if content:
        ire = bars(ire, region, standard)
    source = sg.lfilter(sg.firwin(81, 6e6, fs=sim.F80), 1, np.repeat(ire, 2))
    freq = sim.PORCH_HZ + offset_hz + dev * source / 140 * sim.DEV_PP
    z80 = np.exp(2j * np.pi * np.cumsum(freq) / sim.F80)
    if echo:
        a, tau = echo
        k = int(round(tau * sim.F80))
        z80 = z80 + a * np.exp(1j * 1.3) * np.r_[np.zeros(k), z80[:-k]]
    if spur:
        f, dbc = spur
        z80 = z80 + 10 ** (dbc / 20) * np.exp(2j * np.pi * f * np.arange(len(z80)) / sim.F80)
    if fade:
        dur, period, depth, start = fade
        t = np.arange(len(z80)) / sim.F80
        env = np.where(((t - start) % period) < dur, 10 ** (depth / 20), 1.)
        z80 = z80 * sg.lfilter(np.ones(400) / 400, 1, env)
    z = sg.sosfilt(sim.CHAN, z80)[::2]
    z /= np.sqrt(np.mean(abs(z) ** 2))
    n = sg.sosfilt(sim.CHAN, (rng.normal(size=2 * len(z)) + 1j * rng.normal(size=2 * len(z))) / np.sqrt(2))[::2]
    n /= np.sqrt(np.mean(abs(n) ** 2))
    pre = sg.butter(4, filter_hz / 2, fs=sim.FS, output='sos') if filter_hz else None
    ratio = 10 ** (cnr / 10)

    def quantize(noise):
        y = (z * np.sqrt(ratio) + noise) / np.sqrt(1 + ratio)
        if pre is not None:
            y = sg.sosfilt(pre, y)
        if iq:
            g_db, ph = iq
            g = 10 ** (g_db / 20)
            y = y.real * g + 1j * (y.imag * np.cos(np.radians(ph)) + y.real * np.sin(np.radians(ph)))
        ref = y if not fade else y[np.abs(z) > .5]          # Direct Gain fade hold
        y = y * rms / np.sqrt(np.mean(abs(ref) ** 2)) + dc * (1 + 1j)
        return sim.quantize_iq(y, 'ultrafine')

    truth = sg.sosfilt(sim.GOGGLE, ire)
    return dict(raw=quantize(n), clean=quantize(np.zeros_like(n)), truth=truth, region=region, standard=standard)


def matrix_specs():
    specs = []
    for dev in (0.69, 1.0, 1.4, 1.7, 2.0):
        prof = S.firmware_profile(dev)
        for sit in SITUATIONS:
            for cnr in (4., 8., 25.):
                for seed in MATRIX_SEEDS:
                    specs.append(dict(split='matrix', dev=dev, prof=prof, sit=sit, cnr=cnr, seed=seed, rms=4.2))
        for seed in MATRIX_SEEDS:
            specs.append(dict(split='matrix', dev=dev, prof=prof, sit='clean', cnr=25., seed=seed, rms=5.6,
                              strong=True))
    return specs


def content_specs():
    return [dict(split='content', dev=dev, prof=S.firmware_profile(dev), sit=f'bars{off:+g}', offset=off * 1e6,
                 cnr=cnr, seed=CONTENT_SEED + i, rms=4.2)
            for dev in (1.4, 1.7, 2.0) for off in (-1.5, 0., 1.5) for i, cnr in enumerate((8., 25.))]


def validation_specs():
    return [dict(spec, split='validation', prof='WIDE', seed=spec['seed'] + VALIDATION_SEED_OFFSET)
            for spec in D.case_specs('validation')]


def job(spec):
    if spec['split'] == 'validation':
        c = sim.make_case(spec['seed'], spec['cnr'], spec['standard'], spec['deviation'], filter_hz=16e6,
                          rms=spec['rms'], offset_hz=spec['offset_hz'], echoes=spec['echoes'],
                          fades=spec['fades'], fields=2, quantizer='ultrafine')
        lut = None
    elif spec['split'] == 'content':
        p = S.PROFILES[spec['prof']]
        std = ('PAL', 'NTSC')[spec['seed'] % 2]
        c = make_case(1000 * spec['seed'] + 7, spec['cnr'], std, spec['dev'], p['filter_hz'],
                      offset_hz=spec['offset'], content=True)
        lut = S.lut(p['lut'])
    else:
        p = S.PROFILES[spec['prof']]
        std = ('PAL', 'NTSC')[spec['seed'] % 2]
        c = make_case(1000 * spec['seed'] + int(10 * spec['cnr']) + 7, spec['cnr'], std, spec['dev'],
                      p['filter_hz'], rms=spec['rms'], **SITUATIONS[spec['sit']])
        lut = S.lut(p['lut'])
    base = sim.measure(c, sim.u85q_codes(c['raw'], lut), sim.u85q_codes(c['clean'], lut))
    new = sim.measure(c, awd6(c['raw']), awd6(c['clean']))
    return dict(spec, baseline=dict(picture=base['picture'], sinad=base['sinad']),
                picture=new['picture'], sinad=new['sinad'])


def groups(rows):
    out = {}
    for name, keep in [('validation_WIDE', lambda r: r['split'] == 'validation'),
                       ('matrix_WIDE', lambda r: r['split'] == 'matrix' and r['prof'] == 'WIDE'),
                       ('matrix_DEFAULT', lambda r: r['split'] == 'matrix' and r['prof'] == 'DEFAULT'),
                       ('matrix_KP060', lambda r: r['split'] == 'matrix' and r['prof'] == 'KP060'),
                       ('matrix_KP050', lambda r: r['split'] == 'matrix' and r['prof'] == 'KP050'),
                       ('content_WIDE', lambda r: r['split'] == 'content' and r['prof'] == 'WIDE'),
                       ('content_DEFAULT', lambda r: r['split'] == 'content' and r['prof'] == 'DEFAULT')]:
        rr = [r for r in rows if keep(r)]
        if rr:
            out[name] = D.assess(rr)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('output', type=Path, help='new directory; never overwritten')
    p.add_argument('--jobs', type=int, default=4)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    text = PROGRAM.read_text()
    protocol = dict(
        design=dict(program='main/awd6.bsasm', generator='tools/gen_awd_demod.py',
                    program_sha256=hashlib.sha256(text.encode()).hexdigest()),
        sources={name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in
                 [('awd_study.py', HERE / 'awd_study.py'), ('sim.py', HERE / 'sim.py'),
                  ('situations.py', HERE / 'situations.py'), ('demod_search.py', HERE / 'demod_search.py'),
                  ('bs_emu.py', ROOT / 'tools' / 'bs_emu.py'),
                  ('gen_awd_demod.py', ROOT / 'tools' / 'gen_awd_demod.py')]},
        validation_seed_offset=VALIDATION_SEED_OFFSET, matrix_seeds=MATRIX_SEEDS, content_seed=CONTENT_SEED,
        situations={k: {kk: list(vv) if isinstance(vv, tuple) else vv for kk, vv in v.items()}
                    for k, v in SITUATIONS.items()},
        baseline='U85Q variant of the firmware profile (WIDE/DEFAULT u85q, KP060, KP050)',
        evidence='synthetic luma SINAD and goggle-model lock; host emulator; no RF/range claim')
    D.save(a.output / 'protocol.json', protocol)
    specs = validation_specs() + content_specs() + matrix_specs()
    rows = []
    with mp.get_context('spawn').Pool(a.jobs) as pool:
        for r in pool.imap(job, specs, chunksize=2):
            rows.append(r)
            if len(rows) % 40 == 0 or len(rows) == len(specs):
                D.save(a.output / 'rows.json', rows)
                print(f'{len(rows)}/{len(specs)}', flush=True)
    summary = groups(rows)
    D.save(a.output / 'summary.json', summary)
    for k, v in summary.items():
        print(k, json.dumps({kk: vv for kk, vv in v.items() if kk != 'violations'}), len(v['violations']))


if __name__ == '__main__':
    main()
