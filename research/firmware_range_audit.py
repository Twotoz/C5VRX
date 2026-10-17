"""C5VRX by Twotoz and contributors: range losses after PR 211.

Extends sim.py, situations.py, best_combo.py and the physical ADC model in
iq_lane_fusion.py. This is synthetic evidence, not measured sensitivity.
Uses targets exported by range_policy.c from the actual firmware header.

python research/firmware_range_audit.py study research/data/range_audit.json
python research/firmware_range_audit.py report research/data/range_audit.json
"""
from pathlib import Path
import json
import hashlib
import os
import multiprocessing as mp
import sys
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sim
from kp_morph import build_lut
from u85q_lp import lp_codes
from best_combo import limit
from iq_lane_fusion import capture

CNRS = tuple(map(float, os.environ.get('CNRS', '-2,0,2,4,6,8,10').split(',')))
DEVS = tuple(map(float, os.environ.get('DEVS', '.69,1,1.4,1.7,2').split(',')))
SEEDS = tuple(map(int, os.environ.get('SEEDS', '51,52').split(',')))
FAMILIES = os.environ.get('FAMILIES', 'profile,offset,quantizer').split(',')
PHYSICAL_ADC = os.environ.get('PHYSICAL_ADC', '0') == '1'
POLICY = {(r['dev'], r['cn'], r['narrow'], r['wide']): r['kp'] / 100
          for r in map(json.loads, filter(None, (HERE / 'data/range_policy.jsonl').read_text(encoding='utf-8').splitlines()))}


def kp(dev, cn, filt):
    return POLICY[(int(round(dev * 100)), int(round(cn * 10)), int(filt == 10), int(filt == 16))]


def policy_fingerprint():
    rows = list(map(json.loads, filter(None, (HERE / 'data/range_policy.jsonl').read_text(encoding='utf-8').splitlines())))
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def measure(c, raw, clean, gain):
    lut = build_lut(gain)
    m = sim.measure(c, lp_codes(raw, lut), lp_codes(clean, lut))
    return {k: float(m[k]) for k in ('picture', 'sinad')}


def job(spec):
    family, dev, cn, seed = spec
    standard = ('PAL', 'NTSC')[seed % 2]
    # Same noise/picture seed within a paired comparison.
    rng_seed = seed * 1000 + int(round(cn * 10)) + 40
    rows = []
    def scene(filt, offset=0.):
        c = sim.make_case(rng_seed, cn, standard, dev, filter_hz=filt * 1e6,
                          offset_hz=offset * 1e6, physical_adc=PHYSICAL_ADC, fields=1)
        if PHYSICAL_ADC:
            scale = 4.2 / np.sqrt(np.mean(abs(c['adc'] / 16.) ** 2))
            c['raw'], c['clean'] = capture(c['adc'] * scale), capture(c['clean_adc'] * scale)
        return c
    def add(label, c, raw=None, clean=None, gain=None, **extra):
        gain = kp(dev, cn, 12) if gain is None else gain
        r = dict(family=family, label=label, dev=dev, cn=cn, seed=seed, standard=standard,
                 physical_adc=PHYSICAL_ADC or family == 'quantizer', kp=gain, **extra)
        r.update(measure(c, c['raw'] if raw is None else raw, c['clean'] if clean is None else clean,
                         gain))
        rows.append(r)
    if family == 'profile':
        for filt, chosen_dev, label in ((12, 0, 'default_unknown'), (12, dev, 'default_measured'),
                                        (10 if dev <= 1.05 else 16 if dev >= 1.6 else 12, dev, 'matched_measured')):
            c = scene(filt)
            add(label, c, gain=kp(chosen_dev, cn, filt), filter_mhz=filt)
    elif family == 'offset':
        filt = 10 if dev <= 1.05 else 16 if dev >= 1.6 else 12
        for off in (-1.5, -.75, 0., .75, 1.5):
            c = scene(filt, off)
            add(f'offset{off:+g}', c, gain=kp(dev, cn, filt), offset_mhz=off, filter_mhz=filt)
    elif family == 'quantizer':
        filt = 10 if dev <= 1.05 else 16 if dev >= 1.6 else 12
        c = sim.make_case(rng_seed, cn, standard, dev, filter_hz=filt * 1e6,
                          physical_adc=True, fields=1)
        # An oracle sets ONE gain per complete case from noisy total power.
        # Clean and noisy inputs share it. Actual Q10 saturation/bit folding
        # precedes Q4 extraction; never substitute ordinary Q4 clipping.
        actual_rms = np.sqrt(np.mean(abs(c['adc'] / 16.) ** 2))
        for radius in (2.5, 3.5, 4.2, 5.6, 7.):
            scale = radius / actual_rms
            raw, clean = capture(c['adc'] * scale), capture(c['clean_adc'] * scale)
            add(f'rms{radius:g}', c, raw, clean, kp(dev, cn, filt), total_rms_cells=radius,
                filter_mhz=filt, oracle_gain=True)
    elif family == 'wide_lp':
        for filt in (12, 16):
            c = scene(filt)
            for gain in (.85, .8, .75):
                add(f'{filt}MHz_kp{gain:.2f}', c, gain=gain, filter_mhz=filt)
    return rows


def study(out):
    specs = [(family, dev, cn, seed) for family in FAMILIES for dev in DEVS
             if family not in ('offset', 'quantizer') or dev in
             ((1., 1.7, 2.) if family == 'offset' else (.69, 1., 1.7))
             for cn in CNRS for seed in SEEDS]
    rows = []
    with mp.get_context('spawn').Pool(int(os.environ.get('JOBS', '4'))) as pool:
        for n, chunk in enumerate(pool.imap_unordered(job, specs), 1):
            rows.extend(chunk)
            if n % 10 == 0 or n == len(specs):
                Path(out).write_text(json.dumps(rows, indent=2), encoding='utf-8')
                print(f'{n}/{len(specs)} cases, {len(rows)} configurations saved', flush=True)
    report(rows)
    Path(out).with_suffix('.meta.json').write_text(json.dumps(dict(
        baseline='14e190d9', policy_sha256=policy_fingerprint(), seeds=SEEDS,
        cnrs=CNRS, deviations=DEVS, families=FAMILIES, rows=len(rows),
        physical_adc=PHYSICAL_ADC, fields=1), indent=2), encoding='utf-8')


def report(rows):
    for family in sorted({r['family'] for r in rows}):
        for dev in sorted({r['dev'] for r in rows if r['family'] == family}):
            group = [r for r in rows if r['family'] == family and r['dev'] == dev]
            print(f'\n{family} deviation {dev:g}: LP with actual firmware kp targets')
            for label in sorted({r['label'] for r in group}):
                xs = [r for r in group if r['label'] == label]
                cns = sorted({r['cn'] for r in xs})
                sinad = [float(np.mean([r['sinad'] for r in xs if r['cn'] == c])) for c in cns]
                lock = [float(np.mean([r['picture'] for r in xs if r['cn'] == c])) for c in cns]
                threshold = limit(cns, sinad, lock)
                print(f'{label:20s} limit={threshold:6.2f} dB; '
                      + ' '.join(f'{c:g}:{s:.2f}/{l:.3f}' for c, s, l in zip(cns, sinad, lock)), flush=True)


def check(export):
    actual = list(map(json.loads, filter(None, Path(export).read_text(encoding='utf-8').splitlines())))
    saved = list(map(json.loads, filter(None, (HERE / 'data/range_policy.jsonl').read_text(encoding='utf-8').splitlines())))
    assert actual == saved, 'range_policy.jsonl does not match current firmware targets'
    for name, count in [('range_audit.json', 630), ('range_audit_wide_heldout.json', 384),
                        ('range_audit_offset_heldout.json', 400)]:
        rows = json.loads((HERE / 'data' / name).read_text(encoding='utf-8'))
        meta = json.loads((HERE / 'data' / name).with_suffix('.meta.json').read_text(encoding='utf-8'))
        assert meta['policy_sha256'] == policy_fingerprint(), f'{name}: stale target-policy evidence'
        assert len(rows) == count, (name, len(rows), count)
        keys = {(r['family'], r['label'], r['dev'], r['cn'], r['seed']) for r in rows}
        assert len(keys) == count, f'{name}: duplicate cases'
        assert all(np.isfinite(r['sinad']) and 0 <= r['picture'] <= 1 for r in rows), name
        for r in rows:
            if r['family'] == 'wide_lp':
                expected = float(r['label'].split('kp')[1])
            else:
                dev = 0 if r['label'] == 'default_unknown' else r['dev']
                expected = kp(dev, r['cn'], r['filter_mhz'])
            assert r['kp'] == expected, f'{name}: target does not match exported firmware policy'
        if 'heldout' in name:
            assert len({r['seed'] for r in rows}) == 4 and all(r['physical_adc'] for r in rows), name
    gates = json.loads((HERE / 'data/range_gate_audit.json').read_text(encoding='utf-8'))
    assert len(gates) == 40 and all(r['probes'] > 0 and 0 <= r['old_gate_pct'] <= r['new_gate_pct'] <= 100 for r in gates)
    print('PASS: actual firmware policy export, 1414 complete synthetic rows, 40 actual-C gate summaries')


if __name__ == '__main__':
    if sys.argv[1] == 'study':
        study(sys.argv[2])
    elif sys.argv[1] == 'report':
        report(json.loads(Path(sys.argv[2]).read_text()))
    elif sys.argv[1] == 'check':
        check(sys.argv[2])
    else:
        raise SystemExit('expected study or report')
