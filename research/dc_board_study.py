"""Receiver DC at the board's real levels (C5VRX by Twotoz and the C5VRX
contributors). Host model on the bit-exact U85Q chain of sim.py.

situations.py scales signal plus noise to the Direct Gain radius and adds at
most 1 cell of DC per axis. The board (2026-10-19, 4 IQ snapshots, A1, no
VTX received, G71) holds its gain where the uncorrected DC point fills the
power band: DC (-0.9, -3.6) cells with receiver noise of only 0.68 cells
per axis, because its DC table was measured in another filter context
(T: DCO_CONTEXT filter_same=0). Here the quantiser input is scaled to a
fixed receiver noise per axis instead, so a weak carrier is weak at the
quantiser too, and the DC is the measured point.

    python dc_board_study.py [out.json]
"""
import json
import multiprocessing as mp
import sys
from pathlib import Path
import numpy as np
from scipy import signal as sg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sim  # noqa: E402
import situations  # noqa: E402

CASES = {
    # label: (noise sd per axis in cells, DC I, DC Q)
    'board G71, DC as measured': (0.68, -0.9, -3.6),
    'board G71, DC removed': (0.68, 0.0, 0.0),
    'G83 (+12 dB), DC removed': (2.7, 0.0, 0.0),
    'walk edge G74, DC as measured': (2.2, 1.8, -1.1),
    'walk edge G74, DC removed': (2.2, 0.0, 0.0),
}
CNRS = (2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 20.0)
SEEDS = (21, 22)


def make_case(seed, cnr_db, standard, deviation, filter_hz, sigma, dc_i, dc_q):
    rng = np.random.default_rng(seed)
    ire, region = sim.raster(standard, seed + 7, 1)
    source = sg.lfilter(sg.firwin(81, 6e6, fs=sim.F80), 1, np.repeat(ire, 2))
    freq = sim.PORCH_HZ + deviation * source / 140 * sim.DEV_PP
    z = sg.sosfilt(sim.CHAN, np.exp(2j * np.pi * np.cumsum(freq) / sim.F80))[::2]
    z /= np.sqrt(np.mean(abs(z) ** 2))
    n = sg.sosfilt(sim.CHAN, (rng.normal(size=2 * len(z)) + 1j * rng.normal(size=2 * len(z))) / np.sqrt(2))[::2]
    n /= np.sqrt(np.mean(abs(n) ** 2))
    pre = sg.butter(4, filter_hz / 2, fs=sim.FS, output='sos')
    ratio = 10 ** (cnr_db / 10)
    noise_after = np.sqrt(np.mean(abs(sg.sosfilt(pre, n)) ** 2))

    def quantize(noise_scale):
        y = sg.sosfilt(pre, z * np.sqrt(ratio) + n * noise_scale)
        # receiver noise per axis = sigma cells (complex rms sigma * sqrt 2)
        y = y * (sigma * np.sqrt(2) / noise_after)
        y = y + dc_i + 1j * dc_q
        i = np.clip(np.floor(y.real), -8, 7).astype(int)
        q = np.clip(np.floor(y.imag), -8, 7).astype(int)
        return (((i & 15) << 4) | (q & 15)).astype(np.uint8)

    truth = sg.sosfilt(sim.GOGGLE, ire)
    return dict(raw=quantize(1.0), clean=quantize(0.0), truth=truth, region=region, standard=standard)


def job(spec):
    label, cnr, seed, lut_name = spec
    sigma, dc_i, dc_q = CASES[label]
    std = ('PAL', 'NTSC')[seed % 2]
    c = make_case(1000 * seed + int(10 * cnr), cnr, std, 0.69, 12e6, sigma, dc_i, dc_q)
    L = situations.lut(lut_name)
    r = sim.measure(c, sim.u85q_codes(c['raw'], L), sim.u85q_codes(c['clean'], L))
    return dict(label=label, kp=lut_name or 'kp0.85', cnr=cnr, seed=seed, picture=r['picture'], sinad=r['sinad'])


def limit(rows):
    """Lowest C/N from which every higher point keeps picture >= 0.9 and SINAD >= 0 dB."""
    by = {}
    for r in rows:
        by.setdefault(r['cnr'], []).append(r)
    ok = {c: min(x['picture'] for x in v) >= 0.9 and np.mean([x['sinad'] for x in v]) >= 0.0 for c, v in by.items()}
    best = None
    for c in sorted(by, reverse=True):
        if not ok[c]:
            break
        best = c
    return best


def main():
    specs = [(l, c, s, k) for l in CASES for c in CNRS for s in SEEDS for k in (None, 'kp0.50')]
    with mp.Pool(max(1, min(4, mp.cpu_count() - 1))) as pool:
        rows = pool.map(job, specs)
    out = {}
    print(f'{"case":34s} {"kp":7s} limit_dB  ' + '  '.join(f'{c:>5.0f}' for c in CNRS) + '   (SINAD dB per C/N)')
    for l in CASES:
        for k in ('kp0.85', 'kp0.50'):
            rs = [r for r in rows if r['label'] == l and r['kp'] == k]
            lim = limit(rs)
            sinads = [np.mean([r['sinad'] for r in rs if r['cnr'] == c]) for c in CNRS]
            out[f'{l} {k}'] = dict(limit_db=lim, sinad=sinads)
            print(f'{l:34s} {k:7s} {str(lim):>8s}  ' + '  '.join(f'{s:5.1f}' for s in sinads))
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(dict(cnrs=CNRS, cases=out, rows=rows), indent=1))


if __name__ == '__main__':
    main()
