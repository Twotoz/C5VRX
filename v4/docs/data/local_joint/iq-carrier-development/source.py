"""C5VRX/Twotoz received-only plateau frequency experiment, offline research.

GPL-3.0-only. Uses the received IQ H clock to find constant-frequency sync
and porch intervals. Multi-lag covariance fitting estimates signal and noise
power without supplied CNR. The nominal channel noise covariance is a design
assumption, not calibrated C5 hardware. No RF write or firmware change.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy import signal as sg
import local_iq_clock as R
import local_joint_observation as J

LAGS = np.arange(9)
FREQUENCIES = np.arange(-10e6, 10e6 + 1, 10000.)


def noise_covariance():
    # Existing simulator: fifth-order +/-10MHz Butterworth,80->40MS/s.
    # Derive its covariance analytically, not from a test case or known CNR.
    impulse = sg.lfilter(*sg.butter(5, 10e6, fs=80e6), np.r_[1., np.zeros(4095)])
    return np.array([np.dot(impulse[:len(impulse)-2*lag], impulse[2*lag:])
                     for lag in LAGS]) / np.dot(impulse, impulse)


def fit_frequency(cov, colored=True):
    lags = LAGS if colored else LAGS[2:]
    observed = cov if colored else cov[2:]
    phasors = np.exp(2j*np.pi*FREQUENCIES[:, None]*lags/R.FS)
    signal_rhs = (phasors.conj()*observed).real.sum(axis=1)
    aa = float(len(lags))
    if colored:
        rho = noise_covariance()
        bb = float(rho@rho)
        ab = (phasors.real*rho).sum(axis=1)
        noise_rhs = float(rho@observed.real)
        det = aa*bb-ab*ab
        signal_power = (bb*signal_rhs-ab*noise_rhs)/np.maximum(det, 1e-12)
        noise_power = (aa*noise_rhs-ab*signal_rhs)/np.maximum(det, 1e-12)
        # Exact two-variable nonnegative LS: boundary if interior is negative.
        negative_signal = signal_power < 0
        negative_noise = noise_power < 0
        signal_power = np.where(negative_signal, 0., signal_power)
        noise_power = np.where(negative_signal, max(0., noise_rhs/bb), noise_power)
        signal_power = np.where(negative_noise, np.maximum(0., signal_rhs/aa), signal_power)
        noise_power = np.where(negative_noise, 0., noise_power)
        predicted = signal_power[:, None]*phasors + noise_power[:, None]*rho
    else:
        signal_power = np.maximum(0., signal_rhs/aa)
        noise_power = np.zeros_like(signal_power)
        predicted = signal_power[:, None]*phasors
    residual = np.sum(abs(predicted-observed)**2, axis=1)
    best = int(np.argmin(residual))
    return dict(frequency_hz=float(FREQUENCIES[best]),
                fitted_signal_power=float(signal_power[best]),
                fitted_noise_power=float(noise_power[best]),
                relative_residual=float(residual[best]/max(float(np.sum(abs(observed)**2)), 1e-12)))


def estimate(raw, clock, stride=127):
    indices = np.arange(0, len(raw)-int(LAGS[-1]), stride)
    # Start-phase is the received contour estimate; no truth delay correction.
    dt = (indices-clock['phase']) % clock['period'] / 40
    masks = dict(sync=(dt>.9)&(dt<3.6),
                 porch=((dt>8.8)&(dt<9.5))|((dt>clock['period']/40-1.3)&(dt<clock['period']/40-.5)))
    result = {}
    for label, mask in masks.items():
        ix = indices[mask]
        samples = R.iq(raw[ix[:, None]+LAGS])
        cov = (samples*samples[:, :1].conj()).mean(axis=0)
        result[label] = dict(samples=len(ix),
            covariance_real=cov.real.tolist(), covariance_imag=cov.imag.tolist(),
            adjacent_hz=float(np.angle(cov[1])*R.FS/(2*np.pi)),
            long_lag=fit_frequency(cov, colored=False),
            colored_covariance=fit_frequency(cov))
    return result


def study(seed, repeats, out):
    import waveforms as V
    conditions = [(6, 1.2, -2e6, 'both')]*repeats + [
        (6, 1.2, 0., 'both'), (4, .69, 0., False), (30, 1.2, 2e6, False)]
    rows = []
    for n, (cnr, dev, offset, stress) in enumerate(conditions):
        for k, standard in enumerate(('PAL', 'NTSC')):
            sample_seed = seed+101*n+17*k
            case = V.make_case(standard, sample_seed, cnr, 4.2,
                lane_model='ultrafine', deviation=dev, cfo_hz=1e6+offset,
                stress=stress, stimulus_seed=sample_seed+1,
                pattern=('zoneplate', 'checker')[k])
            crop = int(np.random.default_rng(sample_seed+3).integers(0, 240000))
            raw = case['raw'][crop:crop+160000]
            clock, _ = R.acquire(raw, stride=127)
            frequencies = estimate(raw, clock)
            # Labels are never given to estimate/fit_frequency. Model plateau
            # frequencies are evaluation labels; filter/transients can differ.
            true = dict(sync=1e6+offset-dev*3.35e6,
                        porch=1e6+offset-dev*30*6.7e6/140)
            errors = {label: dict(adjacent_hz=frequencies[label]['adjacent_hz']-true[label],
                long_lag_hz=frequencies[label]['long_lag']['frequency_hz']-true[label],
                colored_covariance_hz=frequencies[label]['colored_covariance']['frequency_hz']-true[label])
                for label in true}
            row = dict(seed=sample_seed, standard=standard, cnr=cnr, deviation=dev,
                offset_hz=offset, stress=stress, crop_raw_bytes=crop,
                input_sha256=hashlib.sha256(raw.tobytes()).hexdigest(),
                clock=clock, correct_standard=clock['standard']==standard,
                frequency_estimates=frequencies, label_frequency_hz=true, errors=errors)
            rows.append(row)
            print(json.dumps(dict(seed=sample_seed, standard=standard,
                locked=clock['lock_declared'], errors=errors)), flush=True)
    target = J.ROOT/'docs/data/local_joint'/out
    target.mkdir(parents=True, exist_ok=True)
    result = dict(seed=seed, repeats=repeats, rows=rows,
        design=dict(stride=127, training_bytes=160000, lags=LAGS.tolist(),
            frequency_grid_step_hz=10000, noise_covariance=noise_covariance().tolist()),
        scope='received_only_plateau_frequency_reference_no_retuning_or_C5_timing_proof',
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (target/'results.json').write_text(json.dumps(result, indent=1))
    (target/'source.py').write_bytes(Path(__file__).read_bytes())
    print('DONE', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=int, default=8100190)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--out', default='iq-carrier-development')
    args = parser.parse_args()
    study(args.seed, args.repeats, args.out)
