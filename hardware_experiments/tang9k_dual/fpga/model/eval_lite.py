"""C5VRX by Twotoz and the C5VRX contributors: picture check of the Tang Nano
1K lite combiner (lite_model.py) against one C5 and the full 9K combiner
(mrc_model.py), same simulation as eval_model.py. Host simulation only.

    python hardware_experiments/tang9k_dual/fpga/model/eval_lite.py
"""
import multiprocessing as mp
import sys
from pathlib import Path

import numpy as np
from scipy import signal as sg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_model as E  # noqa: E402
import lite_model  # noqa: E402
import mrc_model  # noqa: E402


def one(job):
    seed, cn, fd = job
    rng = np.random.default_rng(seed)
    ire, region = E.sim.raster('NTSC', seed + 7, 1)
    src = sg.lfilter(sg.firwin(81, 6e6, fs=E.F80), 1, np.repeat(ire, 2))
    freq = E.sim.PORCH_HZ + 0.7 * src / 140 * E.sim.DEV_PP
    z80 = sg.sosfilt(E.sim.CHAN, np.exp(2j * np.pi * np.cumsum(freq) / E.F80))
    z80 /= np.sqrt(np.mean(abs(z80) ** 2))
    A = E.branch(z80, cn, rng, fd, rng.uniform(-10e3, 10e3))
    B = E.branch(z80, cn, rng, fd, 15e3)
    c = dict(truth=sg.sosfilt(E.sim.GOGGLE, ire), region=region, standard='NTSC')
    variants = {
        'one_C5_lite': (lite_model.run(A[0], None, False), lite_model.run(A[1], None, False)),
        'mrc_9k': (mrc_model.run(A[0], B[0])[0], mrc_model.run(A[1], B[1])[0]),
        'mrc_1k_lite': (lite_model.run(A[0], B[0]), lite_model.run(A[1], B[1])),
    }
    return job, {n: tuple(E.sim.measure(c, v, w)[k] for k in ('picture', 'sinad')) for n, (v, w) in variants.items()}


if __name__ == '__main__':
    lite_model.g_table()
    jobs = [(s * 100 + cn, cn, fd) for fd in (0, 300) for cn in (0, 2, 4, 6, 9) for s in (21, 22, 23)]
    with mp.Pool() as pool:
        out = pool.map(one, jobs)
    names = ['one_C5_lite', 'mrc_9k', 'mrc_1k_lite']
    print('fading_hz cn_db  ' + '  '.join(f'{n:>16}' for n in names) + '   (picture / SINAD dB)')
    for fd in (0, 300):
        for cn in (0, 2, 4, 6, 9):
            rs = [r for j, r in out if j[1] == cn and j[2] == fd]
            print(f'{fd:9d} {cn:5d}  ' + '  '.join(
                f"{np.mean([r[n][0] for r in rs]):.2f} / {np.mean([r[n][1] for r in rs]):5.1f}".rjust(16) for n in names))
