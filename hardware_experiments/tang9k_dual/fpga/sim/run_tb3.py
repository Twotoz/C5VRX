"""C5VRX by Twotoz and the C5VRX contributors: bit-exact check of
fpga/rtl/mrc3_core.v (Tripleversity, three C5s with 3-bit I/Q) against
fpga/model/mrc3_model.py with Icarus Verilog. Stimulus: three simulated C5
branches (fpga/model/eval3.py), three, two and one branch enabled, plus
random raw words. Exit status 1 on any mismatch after the warm-up.

    python hardware_experiments/tang9k_dual/fpga/sim/run_tb3.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy import signal as sg

HERE = Path(__file__).resolve().parent
RTL = HERE.parent / 'rtl'
sys.path.insert(0, str(HERE.parent / 'model'))
import eval3  # noqa: E402
import mrc3_model  # noqa: E402

WARMUP = 256


def stimulus(seed, cn, fd, n):
    rng = np.random.default_rng(seed)
    E = eval3.E
    ire, _ = E.sim.raster('NTSC', seed, 1)
    src = sg.lfilter(sg.firwin(81, 6e6, fs=80e6), 1, np.repeat(ire[:n + 400], 2))
    z80 = sg.sosfilt(E.sim.CHAN, np.exp(2j * np.pi * np.cumsum(E.sim.PORCH_HZ + 0.7 * src / 140 * E.sim.DEV_PP) / 80e6))
    z80 /= np.sqrt(np.mean(abs(z80) ** 2))
    return [mrc3_model.to_raw3(eval3.branch_y(z80, cn, rng, fd)[0])[:n] for _ in range(3)]


def run(raws, en, work):
    for name, r in zip('abc', raws):
        (work / f'raw_{name}.hex').write_text('\n'.join(f'{int(v):02x}' for v in r) + '\n')
    subprocess.run(['vvp', '-n', str(work / 'tb'), f'+n={len(raws[0])}', f'+en_b={en[1]}', f'+en_c={en[2]}'],
                   cwd=work, check=True, stdout=subprocess.DEVNULL)
    return np.array([int(x, 16) if 'x' not in x and 'z' not in x else -1
                     for x in (work / 'code.hex').read_text().split()], np.int64)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        subprocess.run(['iverilog', '-g2012', '-I', str(RTL), '-o', str(work / 'tb'),
                        str(HERE / 'tb_mrc3_core.v'), str(RTL / 'mrc3_core.v'), str(RTL / 'cordic_angle.v'),
                        str(RTL / 'cordic_iter.v'), str(RTL / 'mul_dsp.v')], check=True)
        ok = True
        cases = [(5, 3, 300, (1, 1, 1)), (6, 12, 0, (1, 1, 1)), (7, -2, 0, (1, 1, 1)),
                 (8, 6, 300, (1, 1, 0)), (9, 6, 300, (1, 0, 1)), (10, 4, 0, (1, 0, 0)), (11, None, 0, (1, 1, 1))]
        for seed, cn, fd, en in cases:
            n = 6000
            if cn is None:   # random raw words, extremes included
                raws = [np.random.default_rng(seed + b).integers(0, 64, n) for b in range(3)]
            else:
                raws = stimulus(seed, cn, fd, n)
            hw = run(raws, en, work)
            ref = mrc3_model.run(raws, tuple(bool(e) for e in en)).astype(np.int64)
            best = None
            for lat in range(5, 40):
                m = np.count_nonzero(hw[lat + WARMUP:lat + len(ref)] != ref[WARMUP:len(hw) - lat])
                if best is None or m < best[1]:
                    best = (lat, m)
            lat, mism = best
            print(f'seed={seed} cn={cn} fd={fd} en={en}: latency {lat}, mismatches {mism} of {len(ref) - WARMUP}')
            ok &= mism == 0
    print('PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
