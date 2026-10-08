"""Offline FM theory experiments for C5VRX by Twotoz and contributors.

Extends the project's adjacent/trajectory/PLL research. These host estimators
are not live BitScrambler programs. Parameters are distinct configurations,
not distinct theories or claims of new mathematical discoveries.
"""
import math
import numpy as np
from numba import njit
from scipy import signal as sg
from scipy.fft import fft
import weak_signal_fit as F

TAU = 2*np.pi
FAMILIES = ('delay', 'cross', 'correlation', 'slope', 'pll', 'kalman', 'ml', 'hybrid')


def configurations(count, seed=2601):
    rng = np.random.default_rng(seed)
    # Expensive periodograms get a smaller quota. Every family remains present.
    quota = [0]*15+[1]*15+[2]*20+[3]*15+[4]*15+[5]*10+[6]+[7]*9
    for j in range(count):
        f = quota[j % 100]
        p = np.zeros(6)
        if f == 0:
            p[:3] = rng.integers(1, 7), rng.uniform(.1, 1), rng.uniform(0, 2)
        elif f == 1:
            p[:4] = rng.integers(0, 3), rng.uniform(.02, 2), rng.uniform(.15, 1), rng.uniform(0, 1)
        elif f == 2:
            p[:4] = rng.integers(1, 16), rng.integers(1, 4), rng.uniform(0, 2), rng.uniform(.1, 1)
        elif f == 3:
            p[:3] = rng.integers(3, 18), rng.uniform(0, 2), rng.uniform(0, 2)
        elif f == 4:
            p[:] = rng.uniform(.15, 1.5), 10**rng.uniform(-2.5, -.1), rng.uniform(0, 3), rng.uniform(.5, np.pi), rng.uniform(0, 1), rng.uniform(0, .01)
        elif f == 5:
            p[:4] = 10**rng.uniform(-3, -.1), 10**rng.uniform(-2, 1), rng.uniform(0, 3), rng.uniform(.5, np.pi)
        elif f == 6:
            p[:4] = rng.integers(3, 14), rng.uniform(0, 2), rng.uniform(.15, 1), rng.uniform(0, 2)
        else:
            p[:4] = rng.integers(3, 14), rng.uniform(.1, 2), rng.uniform(.1, 2), rng.uniform(.15, 1)
        yield dict(name=f'theory-{j:06d}-{FAMILIES[f]}', family=FAMILIES[f], params=p.tolist(),
                   feasibility='offline reference; no C5 realtime schedule demonstrated')


@njit(cache=True)
def wrap(x):
    return (x+np.pi) % (2*np.pi)-np.pi


@njit(cache=True)
def kernel(z, family, p):
    n = len(z)
    out = np.zeros(n)
    centre = 2*np.pi*1e6/40e6  # Fixed prior; never read the target/noiseless phase.
    if family == 0:
        lag = int(p[0])
        for k in range(lag, n):
            v = z[k]*z[k-lag].conjugate()
            conf = abs(v)/(abs(v)+p[2]*p[2]+1e-12)
            out[k] = centre+conf*(math.atan2(v.imag, v.real)/lag-centre)
    elif family == 1:
        for k in range(1, n):
            v = z[k]*z[k-1].conjugate()
            normal = abs(v)+p[1]*p[1]
            x = min(1., max(-1., v.imag/normal))
            if int(p[0]) == 0:
                value = math.asin(x)
            elif int(p[0]) == 1:
                value = x
            else:
                value = v.imag/(abs(v.real)+p[1]*p[1])
            conf = abs(v)/(abs(v)+p[3]+1e-12)
            out[k] = centre+conf*(value-centre)
    elif family == 2 or family == 7:
        window = int(p[0])
        lag = int(p[1]) if family == 2 else 1
        gamma = p[2] if family == 2 else 1.
        products = np.zeros(n, dtype=np.complex128)
        mass = np.zeros(n)
        acc = 0j
        weight = 0.
        for k in range(lag, n):
            v = z[k]*z[k-lag].conjugate()
            products[k] = v/(abs(v)+1e-12)**(1-gamma)
            mass[k] = abs(products[k])
            acc += products[k]
            weight += mass[k]
            if k >= window:
                acc -= products[k-window]
                weight -= mass[k-window]
            value = math.atan2(acc.imag, acc.real)/lag
            if family == 7:
                short = math.atan2(v.imag, v.real)
                coherence = abs(acc)/(weight+1e-12)
                amplitude = abs(v)/(abs(v)+p[1]*p[1]+1e-12)
                agreement = math.exp(-abs(wrap(short-value))/p[2])
                mix = min(1., max(0., amplitude*agreement+(1-amplitude)*(1-coherence)))
                value = mix*short+(1-mix)*value
            out[k] = value
    elif family == 3:
        window = int(p[0])
        phase = np.zeros(n)
        for k in range(1, n):
            v = z[k]*z[k-1].conjugate()
            phase[k] = phase[k-1]+math.atan2(v.imag, v.real)
        for k in range(window-1, n):
            sw, sx, sy, sxx, sxy = 0., 0., 0., 0., 0.
            for j in range(window):
                idx = k-window+1+j
                w = abs(z[idx])**p[1]
                sw += w; sx += w*j; sy += w*phase[idx]
                sxx += w*j*j; sxy += w*j*phase[idx]
            out[k] = (sw*sxy-sx*sy)/(sw*sxx-sx*sx+p[2]+1e-12)
    elif family == 4:
        phase, freq = 0., centre
        for k in range(n):
            predicted = wrap(phase+freq)
            error = wrap(math.atan2(z[k].imag, z[k].real)-predicted)
            error = min(p[3], max(-p[3], error))
            conf = abs(z[k])**2/(abs(z[k])**2+p[2]*p[2]+1e-12)
            freq = (1-p[5])*freq+p[5]*centre+p[1]*conf*error
            freq = min(np.pi*.8, max(-np.pi*.8, freq))
            phase = wrap(predicted+p[0]*conf*error)
            out[k] = freq+p[4]*p[0]*conf*error
    elif family == 5:
        freq, variance = centre, 1.
        for k in range(1, n):
            v = z[k]*z[k-1].conjugate()
            observed = math.atan2(v.imag, v.real)
            measurement = p[1]*(1+p[2]*p[2]/(abs(v)+1e-12))
            variance += p[0]
            gain = variance/(variance+measurement)
            innovation = min(p[3], max(-p[3], wrap(observed-freq)))
            freq += gain*innovation
            variance *= 1-gain
            out[k] = freq
    # Additional post-estimator smoothing is part of the candidate, never
    # silently changed by C/N. The strong-signal tests expose detail loss.
    alpha = p[1] if family == 0 else p[2] if family == 1 else p[3] if family in (2, 7) else 1.
    for k in range(1, n):
        out[k] = alpha*out[k]+(1-alpha)*out[k-1]
    return out


def estimate(z, model):
    family = FAMILIES.index(model['family'])
    p = np.array(model['params'])
    if family != 6:
        return kernel(z, family, p)
    # Local constant-frequency maximum-likelihood periodogram. Grid/finite
    # windows and parabolic interpolation are approximations, not exact ML
    # for arbitrary modulated video. All operations use received IQ only.
    window = int(p[0])
    x = z/(abs(z)+1e-12)**(1-p[1])
    frames = np.lib.stride_tricks.sliding_window_view(x, window)
    power = abs(fft(frames, n=64, axis=-1))**2
    bins = np.r_[np.arange(49, 64), np.arange(0, 16)]
    peak = bins[np.argmax(power[:, bins], axis=1)]
    rows = np.arange(len(peak))
    a, b, c = power[rows, (peak-1) % 64], power[rows, peak], power[rows, (peak+1) % 64]
    correction = np.clip(.5*(a-c)/np.minimum(a-2*b+c, -1e-20), -.5, .5)
    freq = ((peak+correction+32) % 64-32)*TAU/64
    local_power = np.mean(abs(frames)**2, axis=1)
    conf = local_power/(local_power+p[3]*p[3]+1e-12)
    freq = TAU*1e6/40e6+conf*(freq-TAU*1e6/40e6)
    out = np.pad(freq, (window-1, 0))
    return sg.lfilter([p[2]], [1, -(1-p[2])], out)


def decode(raw, model):
    i, q = F.D.cells(raw)
    z = i+.5+1j*(q+.5)
    freq = estimate(z, model)
    # Common nominal Phase50-to-DAC transfer and physical resistor values.
    # Host references process all acquired samples at unique40. This is a
    # deliberate upper-bound geometry, not the live pair20 hardware schedule.
    code = np.rint(np.clip((freq*256/np.pi-F.P.OFFSET)*F.P.SCALE, 0, 63)).astype(int)
    return F.D.goggle(F.B.DAC_VOLTS[code])
