"""C5VRX by Twotoz/contributors: reproducible D32-R2-LP composite study.

Same IQ/calibration procedure for all three decoders. Synthetic sim.py model,
not a physical receiver measurement. Full two-field PAL/NTSC sources, three
fixed seeds, 12 MHz extra analog filter, legacy clipped Q4 model (not a gain
or folding study). Luma is explicitly 2 MHz LP, separate from composite SINAD.
Colour-killer risk is a declared proxy, never an emulated commercial goggle.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import sys
import scipy
import numba
import numpy as np
from scipy import signal as sg
import d32_lp
import sim

CNRS = (-2, 0, 2, 4, 6, 8, 12, 16, 20, 30)
DEVIATIONS = (.7, 1., 1.4, 1.7)
SEEDS = (3202, 3203, 3204)
LUMA = sg.butter(4, 2e6, fs=sim.FS, output='sos')
DETAIL = sg.butter(4, (2.5e6, 5e6), btype='bandpass', fs=sim.FS, output='sos')


def burst_metrics(y, truth, region, standard):
    mask = region == 3
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    fsc = 4433618.75 if standard == 'PAL' else 3579545.454545
    values, refs = [], []
    for a, b in zip(starts, ends):
        if a < 3000 or b > len(y)-3000:
            continue
        t = np.arange(a+12, b-12) / sim.FS
        design = np.c_[np.sin(2*np.pi*fsc*t), np.cos(2*np.pi*fsc*t), np.ones(len(t))]
        v = np.linalg.lstsq(design, y[a+12:b-12], rcond=None)[0]
        r = np.linalg.lstsq(design, truth[a+12:b-12], rcond=None)[0]
        rotation = np.conj(complex(r[0], r[1])) / abs(complex(r[0], r[1]))
        values.append(complex(v[0], v[1]) * rotation)
        refs.append(abs(complex(r[0], r[1])))
    values = np.array(values)
    coherent = np.mean(values)
    amplitude = abs(coherent)
    snr = 10*np.log10(max(amplitude**2, 1e-12) / max(np.mean(abs(values-coherent)**2), 1e-12))
    ratio = amplitude / np.mean(refs)
    # Explicit engineering proxy: coherent burst < half nominal amplitude,
    # or burst phasor SNR < 6 dB. Actual goggles have different AGC/killers.
    return dict(burst_ire=float(amplitude), burst_gain_db=float(20*np.log10(max(ratio, 1e-12))),
                burst_snr_db=float(snr), colour_killer_risk=float(ratio < .5 or snr < 6.))


def sync_metrics(y, truth, region):
    edges = np.diff(np.r_[False, region == 2, False].astype(np.int8))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    normal = [(a,b) for a,b in zip(starts,ends) if 140 < b-a < 320 and a > 3000 and b < len(y)-3000]
    ys = sg.sosfilt(sim.SYNC_LP, y)
    edges = np.diff(np.r_[False, ys < -20, False].astype(np.int8))
    falls, rises = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    accepted = (rises-falls >= 80) & (rises-falls <= 280)
    falls, rises = falls[accepted], rises[accepted]
    widths, depths = [], []
    for a,b in normal:
        idx = np.searchsorted(falls, a-40)
        if idx < len(falls) and falls[idx] <= a+60:
            widths.append(abs((rises[idx]-falls[idx]) - (b-a))/40.)
        depths.append(float(-np.mean(y[a+40:b-40])))
    return dict(sync_integrity=len(widths)/len(normal), sync_depth_ire=float(np.median(depths)),
                sync_width_mae_us=float(np.mean(widths)) if widths else None)


def one_case(args):
    standard, deviation, cnr, seed = args
    c = sim.make_case(seed, cnr, standard, deviation, rms=4.2, filter_hz=12e6, fields=2)
    d32 = sim.u85q_lut(d32_lp.ROOT/'main/d32.bsasm')
    u50 = sim.u85q_lut(d32_lp.ROOT/'main/u50p64.bsasm')
    outputs = {}
    for name, decoder in (('D32-R2', lambda raw: sim._pair_codes(raw,d32,sim.PAIR_BITS,5)),
                          ('D32-R2-LP', lambda raw: d32_lp.codes(raw,d32)),
                          ('U50P64-R1', lambda raw: sim._pair_codes(raw,u50,sim.PAIR_BITS,4))):
        noisy, clean = decoder(c['raw']), decoder(c['clean'])
        y, yc = sim.video_out(noisy), sim.video_out(clean)
        calibration = sim.calibrate(yc,c['truth'],c['region'])
        yi, yci = sim.apply_cal(y,calibration), sim.apply_cal(yc,calibration)
        mask = c['region'][3000:-3000] == 1
        def sinad(a,b):
            a,b = a[3000:-3000][mask],b[3000:-3000][mask]
            return float(10*np.log10(np.var(b)/max(np.mean((a-b)**2),1e-12)))
        yt, tt = sg.sosfilt(LUMA,yi),sg.sosfilt(LUMA,c['truth'])
        detail, detail_truth = sg.sosfilt(DETAIL,yci)[3000:-3000][mask], sg.sosfilt(DETAIL,c['truth'])[3000:-3000][mask]
        metrics = dict(luma_sinad_db=sinad(yt,tt), composite_sinad_db=sinad(yi,c['truth']),
                       picture_lock=min(sim.goggle(yi,c['truth'],standard,'ideal'), sim.goggle(yi,c['truth'],standard,'peak')),
                       clean_detail_gain_db=float(10*np.log10(np.mean(detail**2)/np.mean(detail_truth**2))),
                       clean_detail_error_ire=float(np.sqrt(np.mean((detail-detail_truth)**2))),
                       calibration_lag_samples=calibration[0])
        metrics.update(burst_metrics(yi,c['truth'],c['region'],standard))
        metrics.update(sync_metrics(yi,c['truth'],c['region']))
        metrics['usable'] = float(metrics['picture_lock'] >= .9 and metrics['luma_sinad_db'] >= 6.)
        outputs[name] = metrics
    return dict(standard=standard,deviation=deviation,cnr_db=cnr,seed=seed,decoders=outputs)


def run(path):
    jobs = [(s,d,c,n) for s in ('NTSC','PAL') for d in DEVIATIONS for c in CNRS for n in SEEDS]
    rows=[]
    with concurrent.futures.ProcessPoolExecutor(max_workers=int(os.environ.get('D32_JOBS','4'))) as pool:
        for row in pool.map(one_case,jobs):
            rows.append(row)
            if len(rows)%len(SEEDS)==0:
                values={name:round(float(np.mean([r['decoders'][name]['luma_sinad_db'] for r in rows[-len(SEEDS):]])),2) for name in row['decoders']}
                print(row['standard'],row['deviation'],row['cnr_db'],values,flush=True)
    result=dict(source_commit='8b60fef0f43f43a5305d941568c5975386867fc0',
                versions=dict(numpy=np.__version__, scipy=scipy.__version__, numba=numba.__version__),
                provenance='C5VRX by Twotoz and the C5VRX contributors; sim.py synthetic model',
                model='field_boundary_v2; clipped Q4; legacy ideal gain normalization; clean per-decoder affine calibration',
                samples='two fields per case, 40 MS/s IQ, DAC20 duplicated TX40, radius 4.2, extra 12 MHz filter',
                seeds=SEEDS,cnrs_db=CNRS,deviations=DEVIATIONS,
                usable_definition='picture_lock >= 0.9 and 2 MHz luma SINAD >= 6 dB; colour checked separately',
                colour_killer_proxy='burst amplitude < 0.5 of filtered truth, or inter-line burst phasor SNR < 6 dB',rows=rows)
    Path(path).write_text(json.dumps(result,indent=2)+'\n')


if __name__ == '__main__':
    run(sys.argv[1] if len(sys.argv)>1 else 'research/data/d32_lp.json')
