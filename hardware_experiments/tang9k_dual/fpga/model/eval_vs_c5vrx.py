"""C5VRX by Twotoz and the C5VRX contributors: Tripleversity against a single
C5VRX receiver, host simulation only.

Same signal and channel for every variant: research/sim.py NTSC raster,
deviation 0.7, independent noise per C5, Rayleigh fading (fd Hz) per antenna,
own LO phase and offset, own 250 us block AGC to a 6-cell radius
(eval3.branch_y). Variants:
  C5VRX D32-R2      one C5, 4-bit ultrafine, the production decoder (bit-exact table)
  C5VRX U50P64-R1   one C5, the range decoder
  1 C5 3bit FPGA    mrc3_model with one C5: the FPGA discriminator alone
  2 C5 3bit (DVR)   mrc3_model with C5 C off: Tripleversity in DVR mode
  3 C5 3bit         mrc3_model: Tripleversity
Lock = sim.measure picture (goggle line lock), SINAD = luma. Not a board,
goggle or range result.

    python hardware_experiments/tang9k_dual/fpga/model/eval_vs_c5vrx.py
"""
import json
import multiprocessing as mp
import sys
from pathlib import Path

import numpy as np
from scipy import signal as sg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[3] / 'research'))
import eval3  # noqa: E402
import eval_model as E  # noqa: E402
import mrc3_model  # noqa: E402
import nl_tracker  # noqa: E402
import u50p64  # noqa: E402

F80 = 80e6
CNS = [-4, -2, 0, 2, 4, 6, 9, 12]
FDS = [0, 100, 300]
SEEDS = [11, 12, 13, 14]
NAMES = ['C5VRX D32-R2', 'C5VRX U50P64-R1', '1 C5 3bit FPGA', '2 C5 3bit (DVR)', '3 C5 3bit']
_t = None


def one(job):
    global _t
    if _t is None:
        _t = {'d32': nl_tracker.table(), 'u50': u50p64.table()}
    seed, cn, fd = job
    rng = np.random.default_rng(seed * 1000 + cn * 10 + fd)
    ire, region = E.sim.raster('NTSC', seed + 7, 1)
    src = sg.lfilter(sg.firwin(81, 6e6, fs=F80), 1, np.repeat(ire, 2))
    z80 = sg.sosfilt(E.sim.CHAN, np.exp(2j * np.pi * np.cumsum(E.sim.PORCH_HZ + 0.7 * src / 140 * E.sim.DEV_PP) / F80))
    z80 /= np.sqrt(np.mean(abs(z80) ** 2))
    ys = [eval3.branch_y(z80, cn, rng, fd) for _ in range(3)]
    c = dict(truth=E.sim.sosfilt(E.sim.GOGGLE, ire) if hasattr(E.sim, 'sosfilt') else sg.sosfilt(E.sim.GOGGLE, ire),
             region=region, standard='NTSC')
    pc = E.sim._pair_codes
    ra, rac = E.to_raw(ys[0][0]), E.to_raw(ys[0][1])
    r3 = [mrc3_model.to_raw3(y[0]) for y in ys]
    r3c = [mrc3_model.to_raw3(y[1]) for y in ys]
    v = {
        'C5VRX D32-R2': (pc(ra, _t['d32'], E.sim.PAIR_BITS, 5), pc(rac, _t['d32'], E.sim.PAIR_BITS, 5)),
        'C5VRX U50P64-R1': (pc(ra, _t['u50'], E.sim.PAIR_BITS, 4), pc(rac, _t['u50'], E.sim.PAIR_BITS, 4)),
        '1 C5 3bit FPGA': (mrc3_model.run(r3[:1]), mrc3_model.run(r3c[:1])),
        '2 C5 3bit (DVR)': (mrc3_model.run(r3, (True, True, False)), mrc3_model.run(r3c, (True, True, False))),
        '3 C5 3bit': (mrc3_model.run(r3), mrc3_model.run(r3c)),
    }
    out = {}
    for k, (a, b) in v.items():
        m = E.sim.measure(c, a, b)
        out[k] = (float(m['picture']), float(m['sinad']))
    return job, out


def cross(cns, vals, level):
    """C/N (dB) where the lock fraction first reaches level (linear interpolation)."""
    for i in range(len(cns)):
        if vals[i] >= level:
            if i == 0:
                return None
            c0, c1, v0, v1 = cns[i - 1], cns[i], vals[i - 1], vals[i]
            return c0 + (level - v0) / (v1 - v0) * (c1 - c0)
    return None


if __name__ == '__main__':
    jobs = [(s, cn, fd) for fd in FDS for cn in CNS for s in SEEDS]
    with mp.Pool() as p:
        res = p.map(one, jobs)
    table = {}
    for fd in FDS:
        for cn in CNS:
            rs = [r for j, r in res if j[1] == cn and j[2] == fd]
            table[(fd, cn)] = {n: (np.mean([r[n][0] for r in rs]), np.mean([r[n][1] for r in rs])) for n in NAMES}
    for fd in FDS:
        print(f'\nfading {fd} Hz' if fd else '\nstatic (no fading)')
        print('C/N dB ' + ''.join(f'{n:>20}' for n in NAMES) + '   (lock / SINAD dB)')
        for cn in CNS:
            print(f'{cn:6d} ' + ''.join(f'{table[(fd, cn)][n][0]:8.2f} /{table[(fd, cn)][n][1]:6.1f}'.rjust(20) for n in NAMES))
        for lvl in (0.5, 0.9):
            xs = {n: cross(CNS, [table[(fd, cn)][n][0] for cn in CNS], lvl) for n in NAMES}
            ref = xs['C5VRX D32-R2']
            print(f'  C/N for lock {lvl:.1f}: ' + ', '.join(
                f"{n} {xs[n]:.1f}" + (f" ({ref - xs[n]:+.1f} dB)" if ref is not None and xs[n] is not None and n != 'C5VRX D32-R2' else '')
                if xs[n] is not None else f'{n} -' for n in NAMES))
    Path(HERE / 'eval_vs_c5vrx.json').write_text(json.dumps(
        {f'{fd}/{cn}': table[(fd, cn)] for fd in FDS for cn in CNS}, indent=0))
