"""C5VRX by Twotoz and the C5VRX contributors: functional check of
fpga/rtl/align_b.v with Icarus Verilog. One FM video-like carrier is sampled
by two free-running clocks (C5 B 40 ppm fast, arbitrary start), each with
its own noise and LO phase. After acquisition en_b must be set, the B
sample handed out with every A sample must lie within one sample of it in
time (whole-sample alignment of a flat FM correlation peak; the host study
showed 25 ns costs nothing), and the combined discriminator error with the
FPGA's pairs must be within 1 % of the error with ideal nearest-in-time
pairs (mrc_model). Exit status 1 on failure.

    python hardware_experiments/tang9k_dual/fpga/sim/run_align_tb.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RTL = HERE.parent / 'rtl'
sys.path.insert(0, str(HERE.parent / 'model'))
from eval_model import to_raw  # noqa: E402
import mrc_model  # noqa: E402

TA = 25.0e-9


def signal(t, rng_seed=3):
    """Phase of a carrier whose frequency follows a band-limited random walk
    between about -3.5 and +1.5 MHz (sync tip to white of an FM video)."""
    rng = np.random.default_rng(rng_seed)
    knots_t = np.arange(0, t.max() + 1e-6, 0.15e-6)
    f = -1e6 + 2.0e6 * np.tanh(rng.normal(size=len(knots_t)))
    grid = np.arange(0, t.max() + 2e-9, 1e-9)
    fg = np.interp(grid, knots_t, f)
    ph = 2 * np.pi * np.cumsum(fg) * 1e-9
    return np.interp(t, grid, ph)


def stream(t, cn_db, lo, rng):
    s = np.exp(1j * (signal(t) + lo))
    r = 10 ** (cn_db / 10)
    n = (rng.normal(size=len(t)) + 1j * rng.normal(size=len(t))) / np.sqrt(2)
    y = (s * np.sqrt(r) + n) / np.sqrt(1 + r)
    return to_raw(y * 6.0)


def main():
    n = 200000
    rng = np.random.default_rng(7)
    pb_ps = 24999                         # 40 ppm fast
    b_start_ps = 7300
    ta = np.arange(n + 10) * TA
    tb = b_start_ps * 1e-12 + np.arange(int(n * 1.001) + 100) * pb_ps * 1e-12
    a = stream(ta, 6, 0.0, rng)
    b = stream(tb, 6, 1.9, rng)
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp)
        (w / 'stim_a.hex').write_text('\n'.join(f'{v:02x}' for v in a) + '\n')
        (w / 'stim_b.hex').write_text('\n'.join(f'{v:02x}' for v in b) + '\n')
        subprocess.run(['iverilog', '-g2012', '-I', str(RTL), '-o', str(w / 'tb'),
                        str(HERE / 'tb_align_b.v'), str(RTL / 'align_b.v'), str(RTL / 'mul_dsp.v')], check=True)
        subprocess.run(['vvp', '-n', str(w / 'tb'), f'+n={n}', f'+pb={pb_ps}', f'+b_start={b_start_ps}'],
                       cwd=w, check=True, stdout=subprocess.DEVNULL)
        rows = [l.split() for l in (w / 'align.txt').read_text().splitlines()]
    a_out = np.array([int(r[1], 16) if 'x' not in r[1] else -1 for r in rows])
    b_out = np.array([int(r[2], 16) if 'x' not in r[2] else -1 for r in rows])
    en = np.array([int(r[3]) if r[3] in '01' else 0 for r in rows])
    first = int(np.argmax(en)) if en.any() else None
    print(f'en_b first set at sample {first} ({first * 25e-9 * 1e3:.2f} ms)' if first else 'en_b never set')
    if first is None:
        print('FAIL')
        sys.exit(1)
    # identify which A / B sample each output is, by 12-sample sequence match
    W = 12
    def index_of(seq, out):
        keys = {}
        for i in range(W, len(seq)):
            keys.setdefault(bytes(seq[i - W + 1:i + 1].astype(np.uint8)), []).append(i)
        idx = np.full(len(out), -1)
        for k in range(W, len(out)):
            win = out[k - W + 1:k + 1]
            if (win < 0).any():
                continue
            hit = keys.get(bytes(win.astype(np.uint8)))
            if hit and len(hit) == 1:
                idx[k] = hit[0]
        return idx
    ia = index_of(a, a_out)
    ib = index_of(b, b_out)
    sel = np.arange(len(rows)) > first + 5000
    good = sel & (ia >= 0) & (ib >= 0)
    dt = (tb[ib[good]] - ta[ia[good]]) / TA
    print(f'checked {good.sum()} samples after lock: dt/sample mean {dt.mean():+.2f}, '
          f'|dt| max {abs(dt).max():.2f}, share |dt| <= 1: {np.mean(abs(dt) <= 1):.4f}, '
          f'en_b share {en[sel].mean():.3f}')
    # end to end: combined discriminator error, FPGA pairs against ideal pairs
    a_cl = np.where(a_out < 0, 0, a_out).astype(np.uint8)
    b_cl = np.where(b_out < 0, 0, b_out).astype(np.uint8)
    s, e = first + 5000, len(rows) - 100
    d = next(d for d in range(8) if np.array_equal(a_cl[s:s + 100], a[s - d:s - d + 100]))
    idx = np.arange(s, e) - d
    ph = signal(ta[:len(a)])
    f = np.diff(ph) / (2 * np.pi) / TA
    true_code = ((np.r_[f[0], f] + 6e6) / 15e6 * 63)[idx][2000:]
    nearest = np.rint((ta[idx] - b_start_ps * 1e-12) / (pb_ps * 1e-12)).astype(int)
    def rms(c):
        return float(np.sqrt(np.mean((c[2000:].astype(float) - true_code) ** 2)))
    e_one = rms(mrc_model.run(a_cl[s:e], None, False)[0])
    e_hw = rms(mrc_model.run(a_cl[s:e], b_cl[s:e])[0])
    e_ideal = rms(mrc_model.run(a[idx], b[nearest])[0])
    print(f'discriminator rms error (codes): one C5 {e_one:.3f}, MRC FPGA pairs {e_hw:.3f}, '
          f'MRC ideal pairs {e_ideal:.3f}')
    ok = (good.sum() > 1000 and np.mean(abs(dt) <= 1.05) > 0.99 and en[sel].mean() > 0.99
          and e_hw <= 1.01 * e_ideal)
    print('PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
