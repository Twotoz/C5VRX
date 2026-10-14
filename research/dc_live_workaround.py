#!/usr/bin/env python3
"""C5VRX by Twotoz/contributors: live DC centering feasibility, offline only.

Extends dsp_circle_dc, kp_morph, physical Q4 capture and archived DCO research.
Prepare IQ; run dc_live_probe.c; report actual-C estimates and bit-exact U85Q-LP.
No invented RF sensitivity or claim of calibration-free analogue DC control.
"""
from pathlib import Path
import argparse
import csv
import json
import struct
import numpy as np
import sim
import iq_lane_fusion as F
from kp_morph import build_lut
from u85q_lp import lp_codes


def centered_lut(kp, dc):
    """Only controller top-five token bits change; worker low eleven identical."""
    lut = build_lut(kp)
    a = np.arange(1024)
    i, q = (a & 15).astype(float), ((a >> 4) & 15).astype(float)
    i -= 16 * (i > 7); q -= 16 * (q > 7)
    z = (i + .5 - dc[0]) + 1j * (q + .5 - dc[1])
    angle = np.angle(z)
    token = np.floor((angle + np.pi) * 28 / (2 * np.pi)).astype(np.int64) % 28
    erased = np.abs(z) < .8
    token[erased] = 28 + (np.floor((angle[erased] + np.pi) / (np.pi / 2)).astype(np.int64) % 4)
    result = (lut & 2047) | (token.astype(np.uint16) << 11)
    assert np.array_equal(result & 2047, lut & 2047)
    if dc == (0., 0.):
        assert np.array_equal(result, lut)
    return result


def probe_windows(raw):
    starts = np.linspace(2048, len(raw) - 256, 32).astype(int)
    return np.concatenate([raw[a:a + 256] for a in starts])


def prepare(folder):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    metadata = []
    with (folder / 'probes.bin').open('wb') as probe:
        for seed in (111, 112, 113, 114):
            for dev, filt, kp in ((.69, 10, .6), (1., 12, .85), (2., 16, .85)):
                for cn in (2, 10, 26):
                    c = sim.make_case(seed, cn, ('PAL', 'NTSC')[seed % 2], dev,
                                      filter_hz=filt * 1e6, fields=1, physical_adc=True)
                    start, count = 50000, 131072
                    adc = c['adc'][start:start + count]
                    truth = c['truth'][start:start + count]
                    region = c['region'][start:start + count]
                    reference = F.capture(c['clean_adc'][start:start + count])
                    key = f'{seed}_{dev:g}_{cn}'
                    np.savez(folder / f'{key}.npz', adc=adc, truth=truth, region=region, reference=reference)
                    for dc in ((0., 0.), (1.5, -.75), (4., -2.)):
                        raw = F.capture(adc + complex(*dc) * 16)
                        id = len(metadata)
                        metadata.append(dict(id=id, seed=seed, dev=dev, filter_mhz=filt,
                                             cn=cn, kp=kp, dc=dc, source=key))
                        samples = probe_windows(raw)
                        probe.write(struct.pack('<II', id, len(samples)) + samples.tobytes())
        captures = sorted((Path(__file__).parent / 'data/walk_2026-10-13').glob('*.bin'))
        current = Path('C:/Users/leonb/AppData/Local/Temp/c5vrx-native-refused-iq40.bin')
        if current.exists(): captures.append(current)
        for path in captures:
            raw = np.frombuffer(path.read_bytes(), np.uint8)
            id = len(metadata)
            metadata.append(dict(id=id, captured=path.name, known_dc=None))
            samples = raw[:8192]
            probe.write(struct.pack('<II', id, len(samples)) + samples.tobytes())
    (folder / 'cases.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(f'Prepared {len(metadata)} actual-C probes; first 108 synthetic held-out scenes')


def report(folder, output):
    folder = Path(folder)
    metadata = json.loads((folder / 'cases.json').read_text())
    with (folder / 'probe.csv').open() as f:
        estimates = {int(r['id']): {k: int(v) for k, v in r.items()} for r in csv.DictReader(f)}
    rows, cache = [], {}
    for meta in metadata:
        fit = estimates[meta['id']]
        if 'captured' in meta:
            rows.append(dict(meta, actual_c=fit)); continue
        key = meta['source']
        if key not in cache:
            z = np.load(folder / f'{key}.npz')
            clean = sim.video_out(lp_codes(z['reference'], build_lut(meta['kp'])))
            cal = sim.calibrate(clean, z['truth'], z['region'])
            mask = z['region'] == 1; mask[:3000] = mask[-3000:] = False
            power = np.var(z['truth'][mask])
            cache[key] = (z, cal, mask, power)
        z, cal, mask, power = cache[key]
        dc = meta['dc']; raw = F.capture(z['adc'] + complex(*dc) * 16)
        mean = (fit['mean_i_mcells'] / 1000, fit['mean_q_mcells'] / 1000)
        center = (fit['dc_i_mcells'] / 1000, fit['dc_q_mcells'] / 1000)
        # Research acceptance, not a production controller: strong circle,
        # no severe rail occupancy; weak/ambiguous scenes keep the original LUT.
        accepted = bool(fit['live_qualified'])
        methods = dict(original=(0., 0.), known_dc=dc, plain_mean=mean,
                       circle=center, gated_circle=center if accepted else (0., 0.))
        scores = {}
        for name, correction in methods.items():
            lut = centered_lut(meta['kp'], tuple(correction))
            y = sim.apply_cal(sim.video_out(lp_codes(raw, lut)), cal)
            error = np.mean((y[mask] - z['truth'][mask]) ** 2)
            scores[name] = float(10 * np.log10(power / max(error, 1e-12)))
        rows.append(dict(meta, actual_c=fit, circle_accepted=accepted,
                         circle_error_cells=float(np.linalg.norm(np.asarray(center) - dc)),
                         mean_error_cells=float(np.linalg.norm(np.asarray(mean) - dc)),
                         luma_sinad_db=scores))
    result = dict(credits='C5VRX by Twotoz and contributors', scope='offline Q4/LUT centering; no physical range claim',
                  seeds=[111, 112, 113, 114], samples=131072, rows=rows)
    Path(output).write_text(json.dumps(result, indent=2), encoding='utf-8')
    for cn in (2, 10, 26):
        group = [r for r in rows if r.get('cn') == cn and r['dc'] == [1.5, -.75]]
        print(f'C/N {cn}: circle median/max error {np.median([r["circle_error_cells"] for r in group]):.3f}/'
              f'{max(r["circle_error_cells"] for r in group):.3f} cells, '
              f'accepted {sum(r["circle_accepted"] for r in group)}/{len(group)}; '
              f'median original/circle/gated SINAD '
              + '/'.join(f'{np.median([r["luma_sinad_db"][m] for r in group]):.2f}'
                         for m in ('original', 'circle', 'gated_circle')))
    for r in rows:
        if 'captured' in r: print(r['captured'], r['actual_c'])
    for cn in (2, 10, 26):
        group = [r for r in rows if r.get('cn') == cn and r['dc'] == [4., -2.]]
        print(f'Stress DC4-2j C/N{cn}: accepted {sum(r["circle_accepted"] for r in group)}/{len(group)}, '
              f'max center error {max(r["circle_error_cells"] for r in group):.3f}, '
              f'median original/known-DC SINAD '
              + '/'.join(f'{np.median([r["luma_sinad_db"][m] for r in group]):.2f}'
                         for m in ('original', 'known_dc')))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('prepare', 'report')); p.add_argument('folder'); p.add_argument('output', nargs='?')
    args = p.parse_args()
    if args.action == 'prepare': prepare(args.folder)
    else: report(args.folder, args.output)
