"""C5VRX by Twotoz and the C5VRX contributors: picture check of the integer
three-branch combiner (mrc3_model.py, 3-bit I/Q per C5) against the
two-branch integer combiner (mrc_model.py, 4-bit) and one C5, with
research/sim.py as eval_model.py (NTSC, deviation 0.7, independent noise,
Rayleigh fading, own LO, 250 us AGC). Host simulation only.

    python hardware_experiments/tang9k_dual/fpga/model/eval3.py
"""
import multiprocessing as mp
import sys
from pathlib import Path

import numpy as np
from scipy import signal as sg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_model as E  # noqa: E402
import mrc3_model  # noqa: E402
import mrc_model  # noqa: E402

F80, FS = 80e6, 40e6


def branch_y(z80, cn, rng, fd, agc_us=250.0):
    n = len(z80)
    s = z80 * (E.rayleigh(n, fd, rng) if fd else 1.0) * np.exp(1j * (rng.uniform(0, 2 * np.pi) + 2 * np.pi * rng.uniform(-10e3, 10e3) * np.arange(n) / F80))
    noise = sg.sosfilt(E.sim.CHAN, (rng.normal(size=n) + 1j * rng.normal(size=n)) / np.sqrt(2))
    noise /= np.sqrt(np.mean(abs(noise) ** 2))
    r = 10 ** (cn / 10)
    y = ((s * np.sqrt(r) + noise) / np.sqrt(1 + r))[::2]
    yc = (s * np.sqrt(r) / np.sqrt(1 + r))[::2]
    blk = int(agc_us * 1e-6 * FS)
    g = np.empty(len(y))
    for k in range(0, len(y), blk):
        g[k:k + blk] = 6.0 / np.sqrt(max(np.mean(abs(y[k:k + blk]) ** 2), 1e-12))
    return y * g, yc * g


def one(job):
    seed, cn, fd = job
    rng = np.random.default_rng(seed)
    ire, region = E.sim.raster('NTSC', seed + 7, 1)
    src = sg.lfilter(sg.firwin(81, 6e6, fs=F80), 1, np.repeat(ire, 2))
    z80 = sg.sosfilt(E.sim.CHAN, np.exp(2j * np.pi * np.cumsum(E.sim.PORCH_HZ + 0.7 * src / 140 * E.sim.DEV_PP) / F80))
    z80 /= np.sqrt(np.mean(abs(z80) ** 2))
    ys = [branch_y(z80, cn, rng, fd) for _ in range(3)]
    c = dict(truth=sg.sosfilt(E.sim.GOGGLE, ire), region=region, standard='NTSC')
    v = {
        '1 C5 4bit': (mrc_model.run(E.to_raw(ys[0][0]), None, False)[0], mrc_model.run(E.to_raw(ys[0][1]), None, False)[0]),
        '2 C5 4bit': (mrc_model.run(E.to_raw(ys[0][0]), E.to_raw(ys[1][0]))[0], mrc_model.run(E.to_raw(ys[0][1]), E.to_raw(ys[1][1]))[0]),
        '3 C5 3bit': (mrc3_model.run([mrc3_model.to_raw3(y[0]) for y in ys]), mrc3_model.run([mrc3_model.to_raw3(y[1]) for y in ys])),
    }
    return job, {k: tuple(E.sim.measure(c, a, b)[m] for m in ('picture', 'sinad')) for k, (a, b) in v.items()}


if __name__ == '__main__':
    jobs = [(s * 100 + cn, cn, fd) for fd in (0, 300) for cn in (0, 2, 4, 6, 9) for s in (71, 72, 73)]
    with mp.Pool() as p:
        res = p.map(one, jobs)
    names = ['1 C5 4bit', '2 C5 4bit', '3 C5 3bit']
    print('fading cn  ' + '  '.join(f'{n:>14}' for n in names) + '   (lock / SINAD dB, integer FPGA models)')
    for fd in (0, 300):
        for cn in (0, 2, 4, 6, 9):
            rs = [r for j, r in res if j[1] == cn and j[2] == fd]
            print(f'{fd:6d} {cn:3d} ' + '  '.join(f"{np.mean([r[n][0] for r in rs]):.2f} / {np.mean([r[n][1] for r in rs]):5.1f}".rjust(14) for n in names))
