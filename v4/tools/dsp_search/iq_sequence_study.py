"""Paired, frozen-calibration sequence-reference ablation for C5VRX.

No training, winner selection, noisy-truth alignment or generated sync.
All decoders receive identical IQ; decoder priors never receive test C/N.
The synthetic AutoFit comparison explicitly supplies deviation/centre to ALL
output mappings, including controls; the inference itself remains IQ-only.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np

import adaptive_particle as A
from amplitude_cases import cases
import iq_sequence as Q
from omega_research import controls
import video_metrics as M
import waveforms as V


def references(particles=512, lag=8, calibration='synthetic-fit'):
    """Predeclared ablations, not candidates from an evolutionary search."""
    fns = controls()
    cfgs = {
        'IQ independent': Q.setup(particles=particles, receiver=False, coloured_noise=False),
        'IQ receiver': Q.setup(particles=particles, coloured_noise=False),
        'IQ receiver AR1': Q.setup(particles=particles),
        'IQ receiver AR1 pair4411': Q.setup(particles=particles, observations='pair4411'),
        f'IQ receiver AR1 lag{lag}': Q.setup(particles=particles, lag=lag),
    }
    for name, cfg in cfgs.items():
        fns[name] = lambda c, cfg=cfg: Q.decode(c['raw'], cfg,
            c['fit']['fit_deviation'], c['fit']['fit_centre_hz'])[0]
    particle = A.setup(particles=particles)
    fns['Existing particle'] = lambda c: A.decode(c['raw'], particle,
        c['fit']['fit_deviation'], c['fit']['fit_centre_hz'])[0]
    if calibration not in ('nominal', 'synthetic-fit'):
        raise ValueError('unknown calibration protocol')
    return fns, cfgs


def freeze_calibration(fns, size):
    """One independent nominal clean record, before any confirmation decoding.

    Existing OVP clean calibration supplies ONE common gain/offset. Only a
    constant per-decoder latency is fitted, on this separate clean record.
    No level/offset or latency is fitted to any confirmation record.
    """
    c = V.make_case('PAL', 3300191, 30, short=True, lane_model='ultrafine',
                    stimulus_seed=3300192, pattern='zoneplate')
    for key in ('raw', 'clean', 'truth', 'region'):
        c[key] = c[key][65536:65536 + max(size, 16384)]
    c['raw'] = c['clean']
    c['fit'] = dict(fit_deviation=1., fit_centre_hz=1e6)
    gain, offset = c['calibration'][1:]
    result = {}
    for name, fn in fns.items():
        y = fn(c)
        lo, hi = 3000, len(y) - 3000
        target = c['truth'][lo:hi]
        errors = [(float(np.mean((gain * y[lo + lag:hi + lag] + offset - target)**2)), lag)
                  for lag in range(-128, 129)]
        error, lag = min(errors)
        if not np.isfinite(error):
            raise ValueError(f'nonfinite calibration for {name}')
        result[name] = (lag, gain, offset)
    return result, dict(iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),
                        seed=c['seed'], samples=len(c['raw']),
                        scope='independent clean nominal record, shared gain/offset, per-model constant lag')


def study_cases(args):
    if args.scenario == 'static':
        yield from cases(seed=args.seed, amplitudes=tuple(args.amplitudes),
                         cnrs=tuple(args.cnr), per=args.per, size=args.size)
        return
    for cnr in args.cnr:
        for k in range(args.per):
            seed = args.seed + 37 * cnr + k
            dev = (.69, 1.)[k % 2]; centre = -436e3 + dev * 1436e3
            for amp in args.amplitudes:
                c = V.make_case(('PAL', 'NTSC')[k % 2], seed, cnr, amp, short=True,
                    cfo_hz=centre, deviation=dev, stimulus_seed=seed + 1,
                    pattern=('zoneplate', 'checker', 'texture', 'osd')[k % 4],
                    lane_model='ultrafine', loss_windows_us=((1920., 2000.), (2420., 2600.)),
                    gain_windows=((2100., 2300., 1.3),), phase_jumps=((2700., 1.1),))
                c['fit'] = dict(fit_deviation=dev, fit_centre_hz=centre)
                for key in ('raw', 'clean', 'truth', 'region'):
                    c[key] = c[key][65536:65536 + args.size]
                c['loss_windows_us'] = tuple((start - 65536 / 40, end - 65536 / 40)
                                             for start, end in c['loss_windows_us'])
                yield c


def recovery(y, c, calibration):
    """Three consecutive matched horizontal pulses, never called goggle lock."""
    aligned, truth, _ = V.calibrated(y, dict(c, calibration=calibration))
    expected, widths = V.pulses(truth)
    actual, awidths = V.pulses(aligned)
    expected = expected[(widths > 140) & (widths < 320)]
    actual = actual[(awidths > 140) & (awidths < 320)]
    scored_start = max(3000, -calibration[0])
    output = []
    for start_us, end_us in c.get('loss_windows_us', ()):
        end = round(end_us * 40) - scored_start
        pulses = expected[(expected >= end) & (expected < end + 512 * 40)]
        passed = np.array([bool(len(actual) and np.min(abs(actual - pulse)) <= 80) for pulse in pulses])
        first = next((int(pulses[j]) for j in range(max(0, len(pulses) - 2))
                      if passed[j:j + 3].all()), None)
        output.append(dict(loss_start_us=start_us, loss_end_us=end_us,
                           three_pulse_recovery_us=(first - end) / 40 if first is not None else None,
                           expected_post_loss_pulses=len(pulses)))
    return output


def measure(y, c, calibration):
    c = dict(c, calibration=calibration)
    aligned, truth, region = V.calibrated(y, c)
    if not np.isfinite(aligned).all():
        raise ValueError('unavailable or nonfinite samples in scored interval')
    result = M.waveform(y, c)
    result['detail'] = M.detail(y, c)
    active = region == 1
    result['active_rmse_ire'] = float(np.sqrt(np.mean((aligned[active] - truth[active])**2)))
    result['active_large_errors_per_1000'] = float(1000 * np.mean(abs(aligned[active] - truth[active]) > 40))
    result['burst'] = M.burst(y, c)
    result['scored_samples'] = len(aligned)
    result['frozen_lag_samples'] = calibration[0]
    result['recovery'] = recovery(y, c, calibration)
    return result


def summary(rows):
    groups = {}
    for row in rows:
        groups.setdefault(f"{row['cnr']}/{row['model']}", []).append(row['metrics'])
    return {key: dict(cases=len(items), sinad_db=float(np.mean([x['sinad'] for x in items])),
                     missed_h=sum(x['h_missing'] for x in items),
                     missed_v=sum(x['v_missing'] for x in items),
                     false_sync_per_line=float(np.mean([x['false_sync_per_line'] for x in items])),
                     detail=float(np.mean([x['detail'] for x in items])),
                     active_rmse_ire=float(np.mean([x['active_rmse_ire'] for x in items])))
            for key, items in groups.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--seed', type=int, default=4300191)
    ap.add_argument('--particles', type=int, default=512)
    ap.add_argument('--lag', type=int, default=8)
    ap.add_argument('--size', type=int, default=16384)
    ap.add_argument('--per', type=int, default=2)
    ap.add_argument('--cnr', type=int, nargs='+', default=[2, 6, 13, 30])
    ap.add_argument('--amplitudes', type=float, nargs='+', default=[3., 7.])
    ap.add_argument('--calibration', choices=('nominal', 'synthetic-fit'), default='synthetic-fit')
    ap.add_argument('--scenario', choices=('static', 'transient'), default='static')
    ap.add_argument('--names', nargs='+', help='explicit decoder subset, recorded before the run')
    args = ap.parse_args()
    if args.output.exists():
        ap.error('use a fresh output directory; retain failed and negative runs')
    if not 8192 <= args.size <= 65536 or args.size % 2 or args.per < 1:
        ap.error('size must be an even 8192..65536; per must be positive')
    if args.scenario == 'transient' and args.size != 65536:
        ap.error('transient scenario requires size 65536 to retain both loss windows and recovery')
    fns, cfgs = references(args.particles, args.lag, args.calibration)
    if args.names:
        if len(set(args.names)) != len(args.names) or any(name not in fns for name in args.names):
            ap.error('names must be distinct declared decoder names')
        fns = {name: fns[name] for name in args.names}
        cfgs = {name: cfg for name, cfg in cfgs.items() if name in fns}
    args.output.mkdir(parents=True)
    protocol = dict(seed=args.seed, particles=args.particles, lag_raw_samples=args.lag,
        size=args.size, per=args.per, cnr=args.cnr, amplitudes=args.amplitudes, scenario=args.scenario,
        calibration=args.calibration, decoders=list(fns),
        configs={name: Q.config_record(cfg) for name, cfg in cfgs.items()},
        input='raw I-high/Q-low uint8 at 40 MS/s, ultrafine lanes',
        truth_access='scorer only; output conversion additionally receives synthetic fit when declared',
        scope='paired synthetic short-record engineering study; not full-field/goggle/RF range validation',
        base_revision='49b926908ca0aae204164949c9b76cbb9b5fc0b8')
    protocol['source_sha256'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path(__file__), Path(Q.__file__), Path(A.__file__), Path(V.__file__), Path(M.__file__))}
    (args.output / 'protocol.json').write_text(json.dumps(protocol, indent=2) + '\n')
    calibrations, calibration_record = freeze_calibration(fns, args.size)
    protocol['frozen_calibrations'] = calibrations
    protocol['calibration_record'] = calibration_record
    (args.output / 'protocol.json').write_text(json.dumps(protocol, indent=2) + '\n')
    rows = []; start = time.monotonic()
    for c in study_cases(args):
        if args.calibration == 'nominal':
            c['fit'] = dict(fit_deviation=1., fit_centre_hz=1e6)
        for name, fn in fns.items():
            then = time.monotonic(); y = fn(c)
            row = dict(seed=c['seed'], cnr=c['cnr'], rms=c['rms'], standard=c['standard'],
                       pattern=c['pattern'], model=name,
                       iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),
                       seconds=time.monotonic() - then, metrics=measure(y, c, calibrations[name]))
            rows.append(row)
            (args.output / 'results.json').write_text(json.dumps(dict(summary=summary(rows), rows=rows), indent=2) + '\n')
        print(f"case {c['seed']} CN={c['cnr']} RMS={c['rms']} {c['standard']} elapsed={time.monotonic()-start:.1f}s", flush=True)
    print(json.dumps(summary(rows), indent=2), flush=True)


if __name__ == '__main__':
    main()
