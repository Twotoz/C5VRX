"""Offline receiver-aware IQ sequence reference for C5VRX.

Extends C5VRX by Twotoz and contributors, especially adaptive_particle.py,
iq_lanes.py and the detector-study channel model. Not realtime C5 firmware.

Each particle retains phase, frequency, slope, amplitude/noise regime, complex
receiver-filter memory and conditional coloured-noise moments. Folded ADC
preimages are integrated, not replaced with a single signed-cell centre.
The noise model is an AR(1) approximation matched to the receiver's lag-one
correlation, NOT the exact Butterworth noise process or measured physical ADC.
Optional fixed-lag estimates follow resampling ancestry. No video timing,
truth, test C/N, supplied gain, sync synthesis or frame replay enters filter().
"""
from dataclasses import dataclass
import math

import numpy as np
from numba import njit
from scipy import signal

from omega_bayes import codes

RATE_HZ = 40_000_000
SHIFTS = {'coarse': 6, 'fine': 5, 'ultrafine': 4}


def cell_intervals(lane):
    """Disjoint analogue intervals per captured nibble, including ADC rails."""
    if lane not in SHIFTS:
        raise ValueError('unknown capture lane')
    shift = SHIFTS[lane]
    adc = np.arange(-512, 512)
    encoded = (((adc & 1023) >> 9) << 3) | (((adc & 1023) >> shift) & 7)
    intervals = []
    for code in range(16):
        indices = np.flatnonzero(encoded == code)
        starts = indices[np.r_[True, np.diff(indices) != 1]]
        ends = indices[np.r_[np.diff(indices) != 1, True]]
        intervals.append([(float('-inf') if start == 0 else (start - 512) / 2**shift,
                           float('inf') if end == 1023 else (end - 511) / 2**shift)
                          for start, end in zip(starts, ends)])
    size = max(map(len, intervals))
    lo = np.zeros((16, size)); hi = np.zeros_like(lo)
    counts = np.array([len(row) for row in intervals], dtype=np.int64)
    for code, row in enumerate(intervals):
        for j, (a, b) in enumerate(row):
            lo[code, j], hi[code, j] = a, b
    return lo, hi, counts


def receiver_model(cutoff_hz=10e6):
    """Known synthetic 80-MS/s filter and output-noise AR(1) approximation."""
    if not 0 < cutoff_hz < RATE_HZ:
        raise ValueError('receiver cutoff must be between 0 and 40 MHz')
    b, a = signal.butter(5, cutoff_hz, fs=2 * RATE_HZ)
    impulse = signal.lfilter(b, a, np.r_[1., np.zeros(8191)])
    rho = float(np.dot(impulse[:-2], impulse[2:]) / np.dot(impulse, impulse))
    return b, a, rho


def setup(particles=512, lag=0, lane='ultrafine', receiver=True,
          coloured_noise=True, cutoff_hz=10e6, innovation=.08, jump=.06,
          temper=1., contamination=.005, observations='full'):
    if particles < 64 or particles > 16384 or int(particles) != particles:
        raise ValueError('particle count must be an integer in [64, 16384]')
    if not isinstance(lag, (int, np.integer)) or not 0 <= lag <= 320:
        raise ValueError('lag must be 0..320 raw samples (0..8 us)')
    if not 0 < temper <= 1 or not 0 <= contamination < 1:
        raise ValueError('invalid likelihood robustness parameters')
    if not np.isfinite(innovation) or innovation <= 0 or not 0 <= jump <= 1:
        raise ValueError('invalid motion prior')
    if observations not in ('full', 'pair4411'):
        raise ValueError('observations must be full or pair4411')
    b, a, rho = receiver_model(cutoff_hz)
    if not receiver:
        b = np.array([1., 0., 0., 0., 0., 0.])
        a = b.copy()
    if not coloured_noise:
        rho = 0.
    regimes = np.array([(amp, sigma) for amp in (.1, 1., 2., 3., 4.8, 7., 10.)
                        for sigma in (.04, .2, .6, 1.5, 3., 6.)])
    lo, hi, counts = cell_intervals(lane)
    return dict(particles=int(particles), lag=int(lag), lane=lane,
                receiver=bool(receiver), coloured_noise=bool(coloured_noise),
                cutoff_hz=float(cutoff_hz), innovation=float(innovation),
                jump=float(jump), temper=float(temper),
                contamination=float(contamination), observations=observations, rho=rho,
                b=b, a=a, regimes=regimes, lo=lo, hi=hi, counts=counts)


@njit(cache=True)
def cell_moments(code, mean, variance, lo, hi, counts):
    """Probability and conditional mean/variance of a Gaussian in a union."""
    sigma = math.sqrt(max(variance, 1e-12))
    mass = 0.; first = 0.; second = 0.
    for j in range(counts[code]):
        aa = (lo[code, j] - mean) / sigma
        bb = (hi[code, j] - mean) / sigma
        # Same-tail subtraction avoids loss from subtracting two CDFs near 1.
        if aa > 0:
            p = .5 * (math.erfc(aa / math.sqrt(2.)) - math.erfc(bb / math.sqrt(2.)))
        else:
            p = .5 * (math.erfc(-bb / math.sqrt(2.)) - math.erfc(-aa / math.sqrt(2.)))
        pa = math.exp(-.5 * aa * aa) / math.sqrt(2 * math.pi) if math.isfinite(aa) else 0.
        pb = math.exp(-.5 * bb * bb) / math.sqrt(2 * math.pi) if math.isfinite(bb) else 0.
        mass += max(p, 0.)
        first += pa - pb
        second += (aa * pa if math.isfinite(aa) else 0.) - (bb * pb if math.isfinite(bb) else 0.)
    if mass < 1e-250:
        return max(mass, 0.), mean, variance
    delta = first / mass
    return mass, mean + sigma * delta, max(variance * (1 + second / mass - delta * delta), 1e-12)


@njit(cache=True)
def _filter(raw, count, lag, b, a, rho, regimes, lo, hi, counts,
            innovation, jump, temper, contamination, pair4411, seed):
    np.random.seed(seed)
    phase = np.random.uniform(0, 2 * np.pi, count)
    freq = np.random.uniform(-7., 9., count)
    slope = np.zeros(count)
    cls = np.arange(count) % len(regimes)
    state = np.zeros((count, 5), np.complex128)
    nm = np.zeros((count, 2)); nv = np.empty((count, 2))
    for j in range(count):
        nv[j, :] = regimes[cls[j], 1] ** 2
    weight = np.full(count, 1. / count)
    history = np.zeros((count, lag + 1))
    ancestors = np.empty(count, np.int64)
    hz = np.full(len(raw) // 2, np.nan)
    diagnostics = np.full((len(raw) // 2, 6), np.nan)
    sign_lo = np.array([[0.], [-np.inf]])
    sign_hi = np.array([[np.inf], [0.]])
    sign_counts = np.ones(2, np.int64)
    # All retained state, including delay history, follows the same ancestry.
    for n in range(len(raw)):
        total = 0.
        slot = n % (lag + 1)
        for j in range(count):
            previous = freq[j]
            if np.random.random() < jump:
                freq[j] += np.random.normal() * 1.8
                slope[j] = 0.
            else:
                slope[j] = .94 * slope[j] + np.random.normal() * innovation
                freq[j] += slope[j]
            freq[j] = max(-10., min(10., freq[j]))
            if np.random.random() < .001:
                old_sigma = regimes[cls[j], 1]
                cls[j] = np.random.randint(len(regimes))
                scale = regimes[cls[j], 1] / old_sigma
                nm[j, :] *= scale; nv[j, :] *= scale * scale
            amp, sigma = regimes[cls[j]]
            # First observed sample is native sample 0; subsequent ones are 2,4,...
            prediction = 0j
            for sub in range(1 if n == 0 else 2):
                f = freq[j] if n == 0 or sub == 1 else .5 * (previous + freq[j])
                phase[j] = (phase[j] + 2 * np.pi * f / 80.) % (2 * np.pi)
                u = amp * complex(math.cos(phase[j]), math.sin(phase[j]))
                prediction = b[0] * u + state[j, 0]
                for k in range(4):
                    state[j, k] = state[j, k + 1] + b[k + 1] * u - a[k + 1] * prediction
                state[j, 4] = b[5] * u - a[5] * prediction
            pi = rho * nm[j, 0]; pq = rho * nm[j, 1]
            vi = rho * rho * nv[j, 0] + (1 - rho * rho) * sigma * sigma
            vq = rho * rho * nv[j, 1] + (1 - rho * rho) * sigma * sigma
            if pair4411 and n % 2 == 1:
                li, mi, ci = cell_moments(raw[n] >> 7, prediction.real + pi, vi,
                                          sign_lo, sign_hi, sign_counts)
                lq, mq, cq = cell_moments((raw[n] >> 3) & 1, prediction.imag + pq, vq,
                                          sign_lo, sign_hi, sign_counts)
                alphabet = 4.
            else:
                li, mi, ci = cell_moments(raw[n] >> 4, prediction.real + pi, vi, lo, hi, counts)
                lq, mq, cq = cell_moments(raw[n] & 15, prediction.imag + pq, vq, lo, hi, counts)
                alphabet = 256.
            likelihood = (1 - contamination) * li * lq + contamination / alphabet
            inlier = (1 - contamination) * li * lq / max(likelihood, 1e-300)
            for axis in range(2):
                prior = pi if axis == 0 else pq
                variance = vi if axis == 0 else vq
                posterior = mi - prediction.real if axis == 0 else mq - prediction.imag
                conditional = ci if axis == 0 else cq
                mean = inlier * posterior + (1 - inlier) * prior
                nm[j, axis] = mean
                nv[j, axis] = max(1e-12, inlier * (conditional + (posterior - mean)**2) +
                                  (1 - inlier) * (variance + (prior - mean)**2))
            weight[j] *= max(likelihood, 1e-300) ** temper
            total += weight[j]
            history[j, slot] = freq[j]
        weight /= max(total, 1e-300)
        square = 0.
        for j in range(count):
            square += weight[j]**2
        source = n - lag
        if source >= 0 and source % 2 == 1:
            k = source // 2; mean = 0.; second = 0.; amplitude = 0.; noise = 0.
            cosine = 0.; sine = 0.
            for j in range(count):
                f = history[j, source % (lag + 1)]
                mean += weight[j] * f; second += weight[j] * f * f
                amplitude += weight[j] * regimes[cls[j], 0]
                noise += weight[j] * regimes[cls[j], 1]
                cosine += weight[j] * math.cos(phase[j]); sine += weight[j] * math.sin(phase[j])
            hz[k] = mean * 1e6
            # Uncertainty refers to source time; phase coherence and A/sigma to arrival time.
            diagnostics[k, :] = np.array([math.sqrt(max(0., second - mean * mean)) * 1e6,
                amplitude, noise, 1 / max(square, 1e-300), math.hypot(cosine, sine), n])
        if square > 2. / count:
            u = np.random.random() / count; cumulative = weight[0]; i = 0
            for j in range(count):
                threshold = u + j / count
                while cumulative < threshold and i < count - 1:
                    i += 1; cumulative += weight[i]
                ancestors[j] = i
            phase = phase[ancestors].copy(); freq = freq[ancestors].copy()
            slope = slope[ancestors].copy(); cls = cls[ancestors].copy()
            state = state[ancestors].copy(); nm = nm[ancestors].copy(); nv = nv[ancestors].copy()
            history = history[ancestors].copy(); weight[:] = 1. / count
    return hz, diagnostics


@dataclass
class Estimate:
    hz: np.ndarray
    diagnostics: np.ndarray
    source_samples: np.ndarray
    available_samples: np.ndarray


def filter(raw, cfg, seed=2300191):
    raw = np.asarray(raw)
    if raw.ndim != 1 or raw.dtype != np.uint8 or len(raw) < 2 or len(raw) % 2:
        raise ValueError('need a nonempty even-length one-dimensional uint8 IQ stream')
    if not 0 <= seed < 2**32:
        raise ValueError('seed must be a uint32')
    hz, diagnostics = _filter(raw, cfg['particles'], cfg['lag'], cfg['b'], cfg['a'],
        cfg['rho'], cfg['regimes'], cfg['lo'], cfg['hi'], cfg['counts'],
        cfg['innovation'], cfg['jump'], cfg['temper'], cfg['contamination'],
        cfg['observations'] == 'pair4411', seed)
    sources = 2 * np.arange(len(hz)) + 1
    return Estimate(hz, diagnostics, sources, sources + cfg['lag'])


def decode(raw, cfg, dev=1., centre=1e6, seed=2300191):
    """Nominal DAC + goggle filter. Supplied dev/centre are declared calibration.

    Unavailable startup/tail samples are NaN, never filled with fabricated sync.
    Delayed outputs retain their actual arrival time; callers must honour it.
    """
    if not np.isfinite(dev) or dev <= 0 or not np.isfinite(centre):
        raise ValueError('invalid output calibration')
    import hardware as H
    estimate = filter(raw, cfg, seed)
    valid = np.isfinite(estimate.hz)
    dac = codes(1e6 + (estimate.hz[valid] - centre) / dev)
    # Keep the existing reference's one-raw-sample publication delay.
    start = 2 + cfg['lag']
    values = np.repeat(H.B.DAC_VOLTS[dac], 2)
    y = np.full(len(raw), np.nan)
    end = min(len(raw), start + len(values))
    if end > start:
        y[start:end] = H.B.D.goggle(values[:end - start])
    return y, estimate


def config_record(cfg):
    return {k: v for k, v in cfg.items() if k not in ('b', 'a', 'regimes', 'lo', 'hi', 'counts')} | {
        'receiver_b': cfg['b'].tolist(), 'receiver_a': cfg['a'].tolist(),
        'regimes': cfg['regimes'].tolist(), 'rate_hz': RATE_HZ,
        'noise_approximation': 'Gaussian assumed-density AR(1); folded intervals marginalized',
        'scope': 'offline approximate reference; no C5 throughput or physical-range proof'}
