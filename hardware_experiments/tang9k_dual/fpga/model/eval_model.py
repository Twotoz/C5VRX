"""C5VRX by Twotoz and the C5VRX contributors: picture check of the integer
TANG9K_DUAL model (mrc_model.py) against a float MRC reference and one C5,
with research/sim.py (NTSC, deviation 0.7, raster, goggle lock and luma
SINAD). Two branches: independent noise, Rayleigh fading (fd Hz), own LO
phase and offset, own 250 us block AGC to a 6-cell radius, 4-bit ultrafine
at 40 MS/s, aligned. Host simulation only, not a board or range result.

    python hardware_experiments/tang9k_dual/fpga/model/eval_model.py
"""
import sys
import multiprocessing as mp
from pathlib import Path

import numpy as np
from scipy import signal as sg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[3] / 'research'))
import sim  # noqa: E402
import mrc_model  # noqa: E402

F80, FS = 80e6, 40e6
TARGET_R = 6.0


def rayleigh(n, fd, rng, paths=16):
    t = np.arange(n) / F80
    h = np.zeros(n, complex)
    for _ in range(paths):
        a = rng.uniform(0, 2 * np.pi)
        h += np.exp(1j * (2 * np.pi * fd * np.cos(a) * t + rng.uniform(0, 2 * np.pi)))
    return h / np.sqrt(paths)


def to_raw(y):
    """4-bit ultrafine byte (I high nibble, Q low) of a signal in cells."""
    def comp(x):
        adc = np.clip(np.floor(x * 16), -512, 511).astype(np.int64)
        return ((adc >> 4) & 7) | (8 * (adc < 0))
    return ((comp(y.real) << 4) | comp(y.imag)).astype(np.uint8)


def branch(z80, cn_db, rng, fd, df_hz, agc_us=250.0):
    n = len(z80)
    s = z80 * (rayleigh(n, fd, rng) if fd else 1.0)
    s = s * np.exp(1j * (rng.uniform(0, 2 * np.pi) + 2 * np.pi * df_hz * np.arange(n) / F80))
    noise = sg.sosfilt(sim.CHAN, (rng.normal(size=n) + 1j * rng.normal(size=n)) / np.sqrt(2))
    noise /= np.sqrt(np.mean(abs(noise) ** 2))
    r = 10 ** (cn_db / 10)
    y = ((s * np.sqrt(r) + noise) / np.sqrt(1 + r))[::2]
    yc = (s * np.sqrt(r) / np.sqrt(1 + r))[::2]
    blk = int(agc_us * 1e-6 * FS)
    g = np.empty(len(y))
    for k in range(0, len(y), blk):
        g[k:k + blk] = TARGET_R / np.sqrt(max(np.mean(abs(y[k:k + blk]) ** 2), 1e-12))
    return to_raw(y * g), to_raw(yc * g)


def float_mrc(ra, rb):
    def cplx(raw):
        b = raw.astype(np.int64)
        return (2 * (((b >> 4) ^ 8) - 8) + 1) + 1j * (2 * ((b & 15 ^ 8) - 8) + 1)
    za, zb = cplx(ra), cplx(rb)

    def snr(z):
        p2 = abs(z) ** 2
        k = np.ones(40) / 40
        m2, m4 = np.convolve(p2, k, 'same'), np.convolve(p2 ** 2, k, 'same')
        s = np.sqrt(np.maximum(2 * m2 ** 2 - m4, 0))
        return s / np.maximum(m2 - s, 1e-3 * m2)
    sa, sb = snr(za), snr(zb)
    a = 1 - np.exp(-1 / 160)
    x = sg.lfilter([a], [1, a - 1], za * np.conj(zb))
    z = np.sqrt(sa * (1 + sa)) * za + np.sqrt(sb * (1 + sb)) * zb * x / np.maximum(abs(x), 1e-9)
    hz = np.angle(z[1:] * np.conj(z[:-1])) * FS / (2 * np.pi)
    return np.clip(np.rint((np.r_[0, hz] + 6e6) / 15e6 * 63), 0, 63).astype(np.uint8)


def one(job):
    seed, cn, fd = job
    rng = np.random.default_rng(seed)
    ire, region = sim.raster('NTSC', seed + 7, 1)
    src = sg.lfilter(sg.firwin(81, 6e6, fs=F80), 1, np.repeat(ire, 2))
    freq = sim.PORCH_HZ + 0.7 * src / 140 * sim.DEV_PP
    z80 = sg.sosfilt(sim.CHAN, np.exp(2j * np.pi * np.cumsum(freq) / F80))
    z80 /= np.sqrt(np.mean(abs(z80) ** 2))
    A = branch(z80, cn, rng, fd, rng.uniform(-10e3, 10e3))
    B = branch(z80, cn, rng, fd, 15e3)
    c = dict(truth=sg.sosfilt(sim.GOGGLE, ire), region=region, standard='NTSC')
    variants = {
        'one_C5': (mrc_model.run(A[0], None, False)[0], mrc_model.run(A[1], None, False)[0]),
        'float_mrc': (float_mrc(*[x[0] for x in (A, B)]), float_mrc(A[1], B[1])),
        'fpga_mrc': (mrc_model.run(A[0], B[0])[0], mrc_model.run(A[1], B[1])[0]),
    }
    res = {}
    for name, (codes, clean) in variants.items():
        r = sim.measure(c, codes, clean)
        res[name] = (r['picture'], r['sinad'])
    return job, res


if __name__ == '__main__':
    jobs = [(s * 100 + cn, cn, fd) for fd in (0, 300) for cn in (0, 2, 4, 6, 9) for s in (21, 22, 23)]
    with mp.Pool() as pool:
        out = pool.map(one, jobs)
    names = ['one_C5', 'float_mrc', 'fpga_mrc']
    print('fading_hz cn_db  ' + '  '.join(f'{n:>16}' for n in names) + '   (picture / SINAD dB)')
    for fd in (0, 300):
        for cn in (0, 2, 4, 6, 9):
            rs = [r for j, r in out if j[1] == cn and j[2] == fd]
            cells = [f'{np.mean([r[n][0] for r in rs]):.2f} / {np.mean([r[n][1] for r in rs]):5.1f}' for n in names]
            print(f'{fd:9d} {cn:5d}  ' + '  '.join(f'{c:>16}' for c in cells))
