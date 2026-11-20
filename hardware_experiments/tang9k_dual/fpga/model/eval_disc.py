"""C5VRX by Twotoz and the C5VRX contributors: which discriminator after the
Tripleversity combiner. The plain per-sample CORDIC phase difference
(mrc3_model) is 3-5 dB behind the C5VRX D32-R2 decoder on one C5
(eval_vs_c5vrx.py). D32-R2's step: the phase difference over 50 ns with
click suppression (a step outside the -110..+135 degree window a video
carrier can make moves the state only 0.7 of the way), on 32 sectors. Here
the same rule runs on the combined angle of the FPGA (16-bit), with 32 or
1024 sectors and a 50 or 25 ns step. Same channel as eval_vs_c5vrx.py.
Host simulation only.

    python hardware_experiments/tang9k_dual/fpga/model/eval_disc.py
"""
import math
import multiprocessing as mp
import sys
from pathlib import Path

import numpy as np
from numba import njit
from scipy import signal as sg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[3] / 'research'))
import eval3  # noqa: E402
import eval_model as E  # noqa: E402
import mrc3_model  # noqa: E402
import nl_tracker  # noqa: E402
from eval_vs_c5vrx import cross  # noqa: E402

F80 = 80e6
CNS = [-8, -6, -4, -2, 0, 2, 4, 6, 9]
FDS = [0, 100, 300]
SEEDS = [11, 12, 13, 14]


@njit(cache=True)
def click_disc(phi, nsec, step, wl, wh, gain, lo, hi):
    n = len(phi)
    out = np.zeros(n, np.uint8)
    s = ((phi[0] + 32768) * nsec >> 16) % nsec
    for k in range(0, n - 2 * step, step):
        t = ((phi[k] + 32768) * nsec >> 16) % nsec
        d = (t - s + nsec // 2) % nsec - nsec // 2
        e = d * 2 * math.pi / nsec
        g = 1.0 if (e >= wl and e <= wh) else gain
        nxt = int(round(s + g * d)) % nsec
        dd = (nxt - s + nsec // 2) % nsec - nsec // 2
        th = dd * 2 * math.pi / nsec
        c = int(round((th - lo) / (hi - lo) * 63))
        c = 0 if c < 0 else (63 if c > 63 else c)
        for j in range(step):
            out[k + step + j] = c
        s = nxt
    return out


def disc(phi, nsec, step):
    t = 25e-9 * step
    lo, hi = 2 * math.pi * -6e6 * t, 2 * math.pi * 9e6 * t
    wl, wh = math.radians(-110 * step / 2), math.radians(135 * step / 2)
    return click_disc(phi, nsec, step, wl, wh, 0.7, lo, hi)


VAR = ['C5VRX D32-R2', '1 C5 fine/50ns', '3 C5 fine/50ns',
       '2 C5 fine/50ns (DVR)']
_t = None


def one(job):
    global _t
    if _t is None:
        _t = nl_tracker.table()
    seed, cn, fd = job
    rng = np.random.default_rng(seed * 1000 + cn * 10 + fd)
    ire, region = E.sim.raster('NTSC', seed + 7, 1)
    src = sg.lfilter(sg.firwin(81, 6e6, fs=F80), 1, np.repeat(ire, 2))
    z80 = sg.sosfilt(E.sim.CHAN, np.exp(2j * np.pi * np.cumsum(E.sim.PORCH_HZ + 0.7 * src / 140 * E.sim.DEV_PP) / F80))
    z80 /= np.sqrt(np.mean(abs(z80) ** 2))
    ys = [eval3.branch_y(z80, cn, rng, fd) for _ in range(3)]
    c = dict(truth=sg.sosfilt(E.sim.GOGGLE, ire), region=region, standard='NTSC')
    out = {}
    for noisy in (0, 1):
        r3 = [mrc3_model.to_raw3(y[noisy]) for y in ys]
        pl3, ph3 = mrc3_model.run(r3, return_phi=True)
        _, ph1 = mrc3_model.run(r3[:1], return_phi=True)
        _, ph2 = mrc3_model.run(r3, (True, True, False), return_phi=True)
        v = {'C5VRX D32-R2': E.sim._pair_codes(E.to_raw(ys[0][noisy]), _t, E.sim.PAIR_BITS, 5),
             '1 C5 fine/50ns': disc(ph1, 1024, 2), '3 C5 plain': pl3, '3 C5 D32/50ns': disc(ph3, 32, 2),
             '3 C5 fine/50ns': disc(ph3, 1024, 2), '3 C5 fine/25ns': disc(ph3, 1024, 1),
             '2 C5 fine/50ns (DVR)': disc(ph2, 1024, 2)}
        out[noisy] = v
    res = {}
    for k in VAR:
        m = E.sim.measure(c, out[0][k], out[1][k])
        res[k] = (float(m['picture']), float(m['sinad']))
    return job, res


if __name__ == '__main__':
    jobs = [(s, cn, fd) for fd in FDS for cn in CNS for s in SEEDS]
    with mp.Pool() as p:
        res = p.map(one, jobs)
    for fd in FDS:
        print(f'\nfading {fd} Hz' if fd else '\nstatic (no fading)')
        print('C/N ' + ''.join(f'{n:>21}' for n in VAR))
        tab = {}
        for cn in CNS:
            rs = [r for j, r in res if j[1] == cn and j[2] == fd]
            tab[cn] = {n: (np.mean([r[n][0] for r in rs]), np.mean([r[n][1] for r in rs])) for n in VAR}
            print(f'{cn:3d} ' + ''.join(f'{tab[cn][n][0]:.2f}/{tab[cn][n][1]:5.1f}'.rjust(21) for n in VAR))
        for lvl in (0.5, 0.9):
            xs = {n: cross(CNS, [tab[cn][n][0] for cn in CNS], lvl) for n in VAR}
            ref = xs['C5VRX D32-R2']
            print(f'  lock {lvl}: ' + ', '.join(f'{n} {ref - xs[n]:+.1f} dB' for n in VAR[1:]
                                                if ref is not None and xs[n] is not None))
