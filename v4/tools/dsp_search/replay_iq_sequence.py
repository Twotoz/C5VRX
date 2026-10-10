"""Replay separately checksummed physical C5 IQ snapshots, without truth scores.

Extends C5VRX by Twotoz/contributors and capture_iq_snapshot.py. Snapshots are
never concatenated: unknown gaps and capture-start state prevent continuity
claims. The receiver model is an assumption supplied on the command line.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from capture_iq_snapshot import fnv1a
import adaptive_particle as A
import iq_sequence as Q
from omega_research import controls


def load_capture(path):
    metadata = json.loads(path.with_suffix('.json').read_text())
    raw = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    if metadata.get('format') != 'Ihigh_Qlow' or int(metadata.get('rate_hz', 0)) != Q.RATE_HZ:
        raise ValueError('capture format/rate does not match the IQ40 model')
    if len(raw) != int(metadata.get('bytes', 0)) or len(raw) < 2048 or len(raw) % 2:
        raise ValueError('need at least 2048 samples, matching length and complete pairs')
    if f'{fnv1a(raw.tobytes()):08x}' != metadata.get('fnv1a'):
        raise ValueError('capture checksum mismatch')
    lanes = {'0': 'coarse', '1': 'fine', '2': 'ultrafine'}
    if str(metadata.get('lane')) not in lanes:
        raise ValueError('unknown capture lane')
    return raw, metadata, lanes[str(metadata['lane'])]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, nargs='+', required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--particles', type=int, default=512)
    ap.add_argument('--lag', type=int, default=8)
    ap.add_argument('--receiver-cutoff-mhz', type=float, default=10.)
    ap.add_argument('--save-arrays', action='store_true', help='save native-timing waveforms and posterior diagnostics')
    args = ap.parse_args()
    if args.output.exists():
        ap.error('use a fresh output directory')
    try:
        captures = [(path, *load_capture(path)) for path in args.input]
        if len({path.stem for path in args.input}) != len(args.input):
            raise ValueError('capture names must be distinct')
        Q.setup(particles=args.particles, lag=args.lag, cutoff_hz=args.receiver_cutoff_mhz * 1e6)
    except (ValueError, OSError, KeyError) as exc:
        ap.error(str(exc))
    args.output.mkdir(parents=True)
    report = dict(scope='offline physical snapshot replay; no truth, continuity, goggle-lock or range validation',
        calibration='fixed nominal deviation=1, centre=1 MHz, no noisy level/lag fit',
        receiver_model='assumed fifth-order Butterworth at 80 MS/s plus matched AR(1) noise; not measured ADC likelihood',
        cutoff_hz=args.receiver_cutoff_mhz * 1e6, particles=args.particles, lag=args.lag,
        source_sha256={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in (Path(__file__), Path(Q.__file__))}, rows=[])
    for path, raw, metadata, lane in captures:
        cfg = Q.setup(particles=args.particles, lag=args.lag, lane=lane,
                      cutoff_hz=args.receiver_cutoff_mhz * 1e6)
        fns = controls()
        # Existing particle likelihood is ultrafine only; never silently use
        # it on another lane. EDGE/PAIR controls also use ultrafine encoding.
        if lane != 'ultrafine':
            fns = {}
        else:
            acfg = A.setup(particles=args.particles)
            fns['Existing particle'] = lambda c: A.decode(c['raw'], acfg)[0]
        case = dict(raw=raw, fit=dict(fit_deviation=1., fit_centre_hz=1e6))
        capture = dict(name=path.name, iq_sha256=hashlib.sha256(raw.tobytes()).hexdigest(),
                       samples=len(raw), duration_us=len(raw) / 40, metadata=metadata,
                       config=Q.config_record(cfg), decoders={})
        for name, fn in fns.items():
            then = time.monotonic(); y = fn(case)
            finite = y[np.isfinite(y)]
            capture['decoders'][name] = dict(seconds=time.monotonic() - then,
                finite_samples=len(finite), output_min_volts=float(finite.min()),
                output_max_volts=float(finite.max()))
            if args.save_arrays:
                np.save(args.output / f'{path.stem}-{name}.npy', y)
        then = time.monotonic(); y, estimate = Q.decode(raw, cfg)
        finite = y[np.isfinite(y)]
        capture['decoders']['IQ receiver AR1'] = dict(seconds=time.monotonic() - then,
            finite_samples=len(finite), output_min_volts=float(finite.min()), output_max_volts=float(finite.max()),
            posterior_std_median_hz=float(np.nanmedian(estimate.diagnostics[512:, 0])),
            inferred_amplitude_median_cells=float(np.nanmedian(estimate.diagnostics[512:, 1])),
            inferred_noise_sigma_median_cells=float(np.nanmedian(estimate.diagnostics[512:, 2])))
        if args.save_arrays:
            np.savez(args.output / f'{path.stem}-sequence.npz', cvbs=y, hz=estimate.hz,
                     diagnostics=estimate.diagnostics, source_samples=estimate.source_samples,
                     available_samples=estimate.available_samples)
        report['rows'].append(capture)
        (args.output / 'replay.json').write_text(json.dumps(report, indent=2) + '\n')
        print(f'replayed {path.name}: {len(raw)} IQ40 bytes, no known-picture score', flush=True)


if __name__ == '__main__':
    main()
