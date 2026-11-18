"""C5VRX by Twotoz and the C5VRX contributors: bit-exact check of
fpga/rtl/mrc_core.v against fpga/model/mrc_model.py with Icarus Verilog.
Stimulus: two simulated C5 branches (fpga/model/eval_model.py), dual and
single-branch. Exit status 1 on any mismatch after the warm-up.

    python hardware_experiments/tang9k_dual/fpga/sim/run_tb.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RTL = HERE.parent / 'rtl'
sys.path.insert(0, str(HERE.parent / 'model'))
import mrc_model  # noqa: E402
import eval_model  # noqa: E402

WARMUP = 256


def stimulus(seed, cn, fd, n):
    rng = np.random.default_rng(seed)
    from scipy import signal as sg
    ire, _ = eval_model.sim.raster('NTSC', seed, 1)
    src = sg.lfilter(sg.firwin(81, 6e6, fs=80e6), 1, np.repeat(ire[:n + 400], 2))
    freq = eval_model.sim.PORCH_HZ + 0.7 * src / 140 * eval_model.sim.DEV_PP
    z = sg.sosfilt(eval_model.sim.CHAN, np.exp(2j * np.pi * np.cumsum(freq) / 80e6))
    z /= np.sqrt(np.mean(abs(z) ** 2))
    a = eval_model.branch(z, cn, rng, fd, 3e3)[0][:n]
    b = eval_model.branch(z, cn, rng, fd, 20e3)[0][:n]
    return a, b


def run(a, b, en_b, work):
    (work / 'raw_a.hex').write_text('\n'.join(f'{v:02x}' for v in a) + '\n')
    (work / 'raw_b.hex').write_text('\n'.join(f'{v:02x}' for v in b) + '\n')
    subprocess.run(['vvp', '-n', str(work / 'tb'), f'+n={len(a)}', f'+en_b={int(en_b)}'],
                   cwd=work, check=True, stdout=subprocess.DEVNULL)
    return np.array([int(x, 16) if 'x' not in x and 'z' not in x else -1
                     for x in (work / 'code.hex').read_text().split()], np.int64)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        subprocess.run(['iverilog', '-g2012', '-I', str(RTL), '-o', str(work / 'tb'),
                        str(HERE / 'tb_mrc_core.v'), str(RTL / 'mrc_core.v'), str(RTL / 'cordic_angle.v'),
                        str(RTL / 'cordic_iter.v'), str(RTL / 'mul_dsp.v')],
                       check=True)
        ok = True
        for seed, cn, fd, en_b in ((5, 3, 300, 1), (6, 12, 0, 1), (7, -2, 0, 1), (8, 6, 300, 0)):
            a, b = stimulus(seed, cn, fd, 6000)
            hw = run(a, b, en_b, work)
            ref = mrc_model.run(a, b, bool(en_b))[0].astype(np.int64)
            best = None
            for lat in range(5, 40):
                m = np.count_nonzero(hw[lat + WARMUP:lat + len(ref)] != ref[WARMUP:len(hw) - lat])
                if best is None or m < best[1]:
                    best = (lat, m)
            lat, mism = best
            print(f'seed={seed} cn={cn} fd={fd} en_b={en_b}: latency {lat}, mismatches {mism} of {len(ref) - WARMUP}')
            ok &= mism == 0
    print('PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
