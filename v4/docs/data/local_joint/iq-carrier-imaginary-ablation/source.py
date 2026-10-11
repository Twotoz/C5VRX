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


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--frozen', default='iq-carrier-coherence-validation')
    parser.add_argument('--out', default='iq-carrier-imaginary-ablation')
    args = parser.parse_args()
    study(args.frozen, args.out)
