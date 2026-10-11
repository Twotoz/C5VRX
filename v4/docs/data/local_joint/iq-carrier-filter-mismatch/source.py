"""C5VRX/Twotoz antisymmetric covariance frequency reference.

GPL-3.0-only. For proper noise through identical real I/Q filters, the
expected noise covariance is real. Im(C(l)) then contains the tone's
S*sin(omega*l), independent of unknown noise power/filter shape. This model
assumption is not proved for the physical C5, ADC folds or I/Q mismatch.
Uses frozen received statistics; truth labels only evaluate errors afterwards.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import local_iq_carrier as C


def fit(cov):
    values = cov.imag[1:]
    sine = np.sin(2*np.pi*C.FREQUENCIES[:, None]*C.LAGS[1:]/C.R.FS)
    power = np.clip((sine@values)/np.maximum(np.sum(sine*sine, axis=1), 1e-12),
                    0., max(0., cov[0].real))
    residual = np.sum((sine*power[:, None]-values)**2, axis=1)
    index = int(np.argmin(residual))
    # A simple exact population identity is recorded as a diagnostic too.
    # Its finite-sample ratio is unstable when Im(C1) is small.
    ratio = cov[2].imag/(2*cov[1].imag) if abs(cov[1].imag)>1e-12 else None
    two_lag = None if ratio is None else float(np.sign(cov[1].imag)*
        np.arccos(np.clip(ratio, -1, 1))*C.R.FS/(2*np.pi))
    return dict(frequency_hz=float(C.FREQUENCIES[index]), signal_power=float(power[index]),
        relative_imag_residual=float(residual[index]/max(float(values@values), 1e-12)),
        two_lag_frequency_hz=two_lag)


def study(frozen, out):
    root = C.J.ROOT/'docs/data/local_joint'
    original_path = root/frozen/'results.json'
    original = json.loads(original_path.read_text())
    rows = []
    for old in original['rows']:
        groups = {}
        for name in ('sync', 'porch'):
            stats = old['frequency_estimates'][name]
            cov = np.array(stats['covariance_real'])+1j*np.array(stats['covariance_imag'])
            prediction = fit(cov)
            prediction['error_hz'] = prediction['frequency_hz']-old['label_frequency_hz'][name]
            prediction['long_lag_error_hz'] = old['errors'][name]['long_lag_hz']
            prediction['two_lag_error_hz'] = (prediction['two_lag_frequency_hz']-
                old['label_frequency_hz'][name]) if prediction['two_lag_frequency_hz'] is not None else None
            groups[name] = prediction
        rows.append(dict(seed=old['seed'], standard=old['standard'], cnr=old['cnr'],
            offset_hz=old['offset_hz'], input_sha256=old['input_sha256'], estimates=groups))
    # Population identity check with arbitrary correlated-noise real covariances.
    # This verifies the stated algebra under its assumptions, not hardware.
    for f in (-7.3e6, -2.1e6, .7e6, 5.6e6):
        phasor = np.exp(2j*np.pi*f*C.LAGS/C.R.FS)
        for shape in (np.exp(-C.LAGS*.05), np.cos(C.LAGS*.8)*np.exp(-C.LAGS*.2)):
            cov = 3*phasor+17*shape
            assert abs(fit(cov)['frequency_hz']-f) < 10001
    report = dict(frozen_dataset=frozen, rows=rows,
        frozen_dataset_sha256=hashlib.sha256(original_path.read_bytes()).hexdigest(),
        algebra_cases=8, scope='frozen_received_statistic_ablation_not_fresh_channel_or_C5_proof',
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    target = root/out
    target.mkdir(parents=True, exist_ok=True)
    (target/'results.json').write_text(json.dumps(report, indent=1))
    (target/'source.py').write_bytes(Path(__file__).read_bytes())
    for group in ('sync', 'porch'):
        print(json.dumps(dict(group=group,
            imaginary_fit_MAE_khz=float(np.mean([abs(r['estimates'][group]['error_hz']) for r in rows[:16]]))/1000,
            long_lag_MAE_khz=float(np.mean([abs(r['estimates'][group]['long_lag_error_hz']) for r in rows[:16]]))/1000)), flush=True)


def channel_study(seed, repeats, out):
    import waveforms as V
    rows = []
    for n in range(repeats):
        for k, standard in enumerate(('PAL', 'NTSC')):
            case_seed = seed+101*n+17*k
            for width in (None, 15359000.):
                case = V.make_case(standard, case_seed, 6, 4.2,
                    lane_model='ultrafine', deviation=1.2, cfo_hz=-1e6,
                    stress='both', stimulus_seed=case_seed+1,
                    pattern=('zoneplate','checker')[k], pre_bw_hz=width)
                crop = int(np.random.default_rng(case_seed+3).integers(0,240000))
                raw = case['raw'][crop:crop+320000]
                clock, _ = C.R.acquire(raw[:160000], stride=127)
                second_clock = dict(clock)
                second_clock['phase'] = (clock['phase']-160000) % clock['period']
                received = C.estimate(raw[160000:], second_clock)
                labels = dict(sync=-5.02e6, porch=-1e6-1.2*30*6.7e6/140)
                estimates = {}
                for group, stats in received.items():
                    cov = np.array(stats['covariance_real'])+1j*np.array(stats['covariance_imag'])
                    imaginary = fit(cov)
                    estimates[group] = dict(imaginary_fit=imaginary,
                        imaginary_error_hz=imaginary['frequency_hz']-labels[group],
                        long_lag_error_hz=stats['long_lag']['frequency_hz']-labels[group],
                        fixed_noise_model_error_hz=stats['colored_covariance']['frequency_hz']-labels[group])
                row = dict(seed=case_seed, standard=standard, extra_pre_bw_hz=width,
                    input_sha256=hashlib.sha256(raw.tobytes()).hexdigest(), clock=clock,
                    adjusted_timing=C.R.timing_metric(clock,case['truth'][crop:crop+320000],160000,20),
                    estimates=estimates)
                rows.append(row)
                print(json.dumps(dict(seed=case_seed,standard=standard,width=width,
                    errors={g:{key:value for key,value in e.items() if key.endswith('_hz')}
                            for g,e in estimates.items()})),flush=True)
    target = C.J.ROOT/'docs/data/local_joint'/out
    target.mkdir(parents=True,exist_ok=True)
    report = dict(seed=seed,repeats=repeats,rows=rows,
        scope='fresh_paired_filter_mismatch_ablation_not_measured_C5_filter_or_hardware_proof',
        gain='simulator_restores_global_RMS_4.2_after_extra_filter_not_physical_AGC',
        noise_model='fixed_original_10MHz_covariance_even_when_extra_filter_changes_it',
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (target/'results.json').write_text(json.dumps(report,indent=1))
    (target/'source.py').write_bytes(Path(__file__).read_bytes())
    print('DONE',flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--frozen', default='iq-carrier-coherence-validation')
    parser.add_argument('--out', default='iq-carrier-imaginary-ablation')
    parser.add_argument('--channel', action='store_true')
    parser.add_argument('--seed', type=int, default=8800190)
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args()
    if args.channel:channel_study(args.seed,args.repeats,args.out)
    else:study(args.frozen, args.out)
