"""Receiver sensitivity from board IQ (C5VRX by Twotoz and the C5VRX
contributors). Host model on the bit-exact U85Q chain of sim.py.

A firmware change helps range only if it lowers the carrier power the
receiver needs for a picture. The analog front end (antenna, LNA, noise
figure) is the same for every firmware, so the comparison can be made
relative to it: this tool reports the **required C/N referred to the fixed
~20.7 MHz chip channel** (the noise of the C5 front end with its widest
filter), measured from what the board actually does:

- receiver noise per axis and DC point at the live gain (IQ snapshots
  taken with no VTX, console I, tools/capture_iq_snapshot.py);
- the live analog filter shape, from the noise spectrum of the same
  snapshots (not from the stored calibration: on 2026-10-19 the stored
  "12.5 MHz" filter flew at 19.7 MHz);
- the digital path (quantisation, sampling), from the noise floor outside
  the filter band.

The bit-exact U85Q chain is then run with exactly that filter, noise and
DC, at the VTX deviation and tracker gain of the run, and the lowest C/N
with picture >= 0.9 and luma SINAD >= 0 dB (both seeds, PAL and NTSC) is the
range limit. Lower is better; 6 dB less is twice the free-space distance.

With an assumed noise figure the limit becomes dBm:
    sensitivity = -174 dBm/Hz + 10 log10(20.7 MHz) + NF + C/N.
NF is NOT measured (default 5 dB, from the C5 Wi-Fi sensitivity); only the
differences between runs are firmware facts.

    python sensitivity_model.py "data/dc_2026-10-19/before_n_*.bin" [--dev 0.69] [--nf 5] [--json out.json]
"""
import argparse
import glob
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

CHANNEL_NBW_HZ = 20.7e6     # the chip channel the C/N is referred to (board, widest filter)
# 1 dB where the limits lie, 2 dB above: small loss terms stay visible.
CNRS = tuple(float(c) for c in list(range(0, 13)) + [14, 16, 18, 20])
SEEDS = (31, 32)


def load_iq(pattern):
    files = sorted(glob.glob(pattern))
    if not files:
        raise SystemExit(f'no snapshots match {pattern}')
    out = []
    for fn in files:
        s = np.fromfile(fn, np.uint8).astype(int)
        i = (((s >> 4) ^ 8) - 8) + 0.5
        q = (((s & 15) ^ 8) - 8) + 0.5
        out.append(i + 1j * q)
    return files, out


def board_state(snaps):
    """Noise, DC, filter magnitude and digital floor of no-carrier snapshots."""
    dcs = np.array([z.mean() for z in snaps])
    sigma = float(np.sqrt(np.mean([(np.var(z.real) + np.var(z.imag)) / 2 for z in snaps])))
    psd = []
    for z in snaps:
        f, p = sg.welch(z - z.mean(), fs=sim.FS, nperseg=256, return_onesided=False, detrend=False)
        psd.append(p)
    f = np.fft.fftshift(f)
    p = np.fft.fftshift(np.mean(psd, 0))
    floor = float(np.median(p[np.abs(f) >= 17e6]))
    thermal = np.clip(p - floor, 0, None)
    peak = float(np.median(thermal[np.abs(f) < 3e6]))
    h2 = thermal / peak
    nbw = float(np.sum(h2) * (f[1] - f[0]))
    digital_db = 10 * np.log10(1 + floor * nbw / (peak * nbw)) if peak > 0 else float('nan')
    return dict(dc_i=float(dcs.real.mean()), dc_q=float(dcs.imag.mean()), dc_abs=float(np.abs(dcs).mean()),
                sigma=sigma, nbw_hz=nbw, digital_loss_db=float(digital_db), f=f, h2=h2)


def fir_from_h2(f, h2, taps=63):
    """Linear-phase complex FIR at 40 MS/s with the measured power response."""
    grid = np.fft.fftfreq(512, 1 / sim.FS)
    mag = np.sqrt(np.clip(np.interp(np.fft.fftshift(grid), f, h2, left=0, right=0), 0, None))
    h = np.fft.fftshift(np.fft.ifft(np.fft.ifftshift(mag)))
    mid = len(h) // 2
    h = h[mid - taps // 2: mid + taps // 2 + 1] * np.hanning(taps)
    return h / np.sqrt(np.sum(np.abs(h) ** 2))


def make_case(seed, cnr_db, standard, deviation, fir, sigma, dc_i, dc_q):
    """C/N referred to the chip channel (sim.CHAN), then the measured filter,
    receiver noise of sigma cells per axis after it, the measured DC."""
    rng = np.random.default_rng(seed)
    ire, region = sim.raster(standard, seed + 7, 1)
    source = sg.lfilter(sg.firwin(81, 6e6, fs=sim.F80), 1, np.repeat(ire, 2))
    freq = sim.PORCH_HZ + deviation * source / 140 * sim.DEV_PP
    z = sg.sosfilt(sim.CHAN, np.exp(2j * np.pi * np.cumsum(freq) / sim.F80))[::2]
    z /= np.sqrt(np.mean(abs(z) ** 2))
    n = sg.sosfilt(sim.CHAN, (rng.normal(size=2 * len(z)) + 1j * rng.normal(size=2 * len(z))) / np.sqrt(2))[::2]
    n /= np.sqrt(np.mean(abs(n) ** 2))
    ratio = 10 ** (cnr_db / 10)
    noise_after = np.sqrt(np.mean(abs(np.convolve(n, fir, 'same')) ** 2))

    def quantize(noise_scale):
        y = np.convolve(z * np.sqrt(ratio) + n * noise_scale, fir, 'same')
        y = y * (sigma * np.sqrt(2) / noise_after) + dc_i + 1j * dc_q
        i = np.clip(np.floor(y.real), -8, 7).astype(int)
        q = np.clip(np.floor(y.imag), -8, 7).astype(int)
        return (((i & 15) << 4) | (q & 15)).astype(np.uint8)

    truth = sg.sosfilt(sim.GOGGLE, ire)
    return dict(raw=quantize(1.0), clean=quantize(0.0), truth=truth, region=region, standard=standard)


def job(spec):
    label, cnr, seed, lut_name, dev, fir, sigma, dc_i, dc_q = spec
    std = ('PAL', 'NTSC')[seed % 2]
    c = make_case(1000 * seed + int(10 * cnr), cnr, std, dev, fir, sigma, dc_i, dc_q)
    L = situations.lut(lut_name)
    r = sim.measure(c, sim.u85q_codes(c['raw'], L), sim.u85q_codes(c['clean'], L))
    return dict(label=label, kp=lut_name or 'kp0.85', cnr=cnr, seed=seed, picture=r['picture'], sinad=r['sinad'])


def limit(rows):
    """Lowest C/N from which every higher point keeps picture >= 0.9 and SINAD >= 0 dB."""
    by = {}
    for r in rows:
        by.setdefault(r['cnr'], []).append(r)
    best = None
    for c in sorted(by, reverse=True):
        v = by[c]
        if min(x['picture'] for x in v) < 0.9 or np.mean([x['sinad'] for x in v]) < 0.0:
            break
        best = c
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('snapshots', nargs='+', help='glob(s) of no-carrier IQ snapshots, one run per glob')
    ap.add_argument('--dev', type=float, default=0.69, help='VTX deviation (T: deviation_x100 / 100)')
    ap.add_argument('--nf', type=float, default=5.0, help='assumed front-end noise figure, dB (not measured)')
    ap.add_argument('--json', help='write the results here')
    a = ap.parse_args()
    runs = []
    for pattern in a.snapshots:
        files, snaps = load_iq(pattern)
        st = board_state(snaps)
        fir = fir_from_h2(st['f'], st['h2'])
        runs.append(dict(pattern=pattern, files=len(files), state=st, fir=fir))
    specs = []
    for k, r in enumerate(runs):
        st = r['state']
        for dc_label, (di, dq) in (('as measured', (st['dc_i'], st['dc_q'])), ('DC removed', (0.0, 0.0))):
            for cnr in CNRS:
                for seed in SEEDS:
                    for lut_name in (None, 'kp0.50'):
                        specs.append((f'{k}|{dc_label}', cnr, seed, lut_name, a.dev, r['fir'], st['sigma'], di, dq))
    with mp.Pool(max(1, min(4, mp.cpu_count() - 1))) as pool:
        rows = pool.map(job, specs)
    ref_db = -174 + 10 * np.log10(CHANNEL_NBW_HZ) + a.nf
    out = []
    print(f'deviation {a.dev}; C/N referred to the {CHANNEL_NBW_HZ / 1e6:.1f} MHz chip channel; '
          f'dBm with an ASSUMED NF of {a.nf:.1f} dB')
    for k, r in enumerate(runs):
        st = r['state']
        res = {}
        for dc_label in ('as measured', 'DC removed'):
            lims = {kp: limit([x for x in rows if x['label'] == f'{k}|{dc_label}' and x['kp'] == kp])
                    for kp in ('kp0.85', 'kp0.50')}
            best = min((v for v in lims.values() if v is not None), default=None)
            res[dc_label] = dict(limit_db=best, per_kp=lims)
        lim = res['as measured']['limit_db']
        nodc = res['DC removed']['limit_db']
        print(f'\n{r["pattern"]} ({r["files"]} snapshots)')
        print(f'  live filter NBW {st["nbw_hz"] / 1e6:.1f} MHz, noise {st["sigma"]:.2f} cells/axis, '
              f'DC ({st["dc_i"]:+.2f}, {st["dc_q"]:+.2f}) = {st["dc_abs"]:.2f} cells, '
              f'digital path {st["digital_loss_db"]:.2f} dB')
        if lim is None:
            print(f'  range limit: above {CNRS[-1]:.0f} dB C/N (no picture in the sweep)')
        else:
            print(f'  range limit {lim:.0f} dB C/N  ->  sensitivity ~{ref_db + lim:.1f} dBm (assumed NF)')
        if nodc is not None and lim is not None:
            print(f'  of which the DC costs {lim - nodc:.0f} dB (limit {nodc:.0f} dB without it)')
        out.append(dict(pattern=r['pattern'], files=r['files'], nbw_mhz=st['nbw_hz'] / 1e6, sigma=st['sigma'],
                        dc=[st['dc_i'], st['dc_q']], digital_loss_db=st['digital_loss_db'], results=res,
                        sensitivity_dbm=None if lim is None else ref_db + lim))
    if a.json:
        Path(a.json).write_text(json.dumps(dict(deviation=a.dev, nf_assumed_db=a.nf, cnrs=CNRS, runs=out,
                                                rows=rows), indent=1))


if __name__ == '__main__':
    main()
