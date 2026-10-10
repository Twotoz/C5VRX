#!/usr/bin/env python3
"""Goggle picture-lock model and full-field cliff measurement (C5VRX by Twotoz/contributors).

Operator problem (2026-10-10): "as soon as the video is only a little bad,
the complete video drops out". Earlier screens scored ~13-line snippets with
pulse counting on the fine lane. A goggle drops the whole picture when its
sync separator / H-PLL loses lock, so this models that stage explicitly and
runs full PAL/NTSC fields on the board's fixed ultrafine lane.

Goggle model (generic analog-video decoder, not a specific product):
- 5 MHz input filter (the existing decode chain), then a 1 MHz sync low-pass;
- sync slicer midway between a tracked sync-tip and blanking level;
- a sync candidate needs >= 1.5 us below the slice (width discrimination);
- H-PLL flywheel: candidates inside a +-1.5 us window around the prediction
  correct the phase (gain 0.3); misses free-run one line period;
- lock lost when more than half of the last 32 lines had no in-window sync,
  regained after 8 consecutive in-window syncs. Unlocked, every candidate
  re-seeds the prediction (that is how noise keeps a goggle from relocking).
A line is displayed correctly when the goggle is locked and its line start is
within 0.5 us of the true sync. Field-level V detection is reported apart.

These are synthetic engineering measurements, not goggle acceptance.
"""
import numpy as np
from scipy import signal as sg

FS = 40e6
SYNC_LP = sg.butter(2, 1e6, fs=FS, output='sos')


def calibrate(y, truth, region, burn=3000):
    """Per-demod clean calibration (stands in for the goggle's sync AGC/clamp):
    integer lag, then least-squares gain/offset on non-burst samples. The lag
    is chosen on every 16th sample, the fit uses all."""
    def fit(lag, step):
        a = y[burn + lag:len(y) - burn + lag:step]; t = truth[burn:len(truth) - burn:step]
        m = region[burn:len(region) - burn:step] != 3
        A = np.c_[a[m], np.ones(m.sum())]
        coef = np.linalg.lstsq(A, t[m], rcond=None)[0]
        return float(np.mean((A @ coef - t[m]) ** 2)), float(coef[0]), float(coef[1])
    lag = min(range(-40, 41), key=lambda l: fit(l, 16)[0])
    _, g, off = fit(lag, 1)
    return lag, g, off


def apply(y, cal):
    lag, g, off = cal
    out = np.empty_like(y)
    if lag >= 0: out[:len(y) - lag] = y[lag:]; out[len(y) - lag:] = y[-1]
    else: out[-lag:] = y[:lag]; out[:-lag] = y[0]
    return g * out + off


def true_syncs(truth):
    below = truth < -20
    d = np.diff(np.r_[False, below, False].astype(np.int8))
    s, e = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    w = e - s
    return s[(w > 140) & (w < 320)], s[w > 600], s


def goggle(y_ire, truth, standard, window_us=1.5, gain=.3, min_width_us=1.5, agc='ideal'):
    period = 2560. if standard == 'PAL' else 40e6 / 15734.264
    ys = sg.sosfilt(SYNC_LP, y_ire)
    hs, vs, all_s = true_syncs(truth)
    # Candidate detection. agc='ideal': slicer fixed at -20 IRE of the clean
    # calibration (perfect sync AGC). agc='peak': diode-style sync-tip clamp
    # (instant attack to a new minimum, ~2 ms release toward the sliced sync
    # level) and back-porch blank tracking, slicer midway - a cheap goggle.
    W = int(min_width_us * 40); n = len(ys)
    if agc == 'ideal':
        thr = np.full(n, -20.)
    else:
        thr = np.empty(n); tipv = -40.; blk = 0.; rel = 1 / (2e-3 * FS)
        lp = sg.sosfilt(sg.butter(1, 2e5, fs=FS, output='sos'), ys)  # clamp sees ~200 kHz
        for k0 in range(0, n, 40):  # 1-us control steps
            seg = lp[k0:k0 + 40]; m = float(seg.min())
            # Diode clamp: a deeper excursion takes the tip at once, then it
            # relaxes toward the nominal sync depth below blanking.
            tipv = m if m < tipv else tipv + (blk - 40. - tipv) * 40 * rel
            thr[k0:k0 + 40] = (tipv + blk) / 2
    below = ys < thr
    cs = np.r_[0, np.cumsum(below)]
    fall = np.flatnonzero(below[1:] & ~below[:-1]) + 1
    cands = []; last = -10 ** 9
    for f in fall:
        if f + W >= n or f - last < W: continue
        if cs[f + W] - cs[f] >= .8 * W:
            cands.append(f); last = f
    cands = np.array(cands, dtype=float)
    # H-PLL flywheel over the candidate stream.
    win = window_us * 40
    locked = False; hits = 0; history = []
    line_start = []  # (predicted line start used for display, locked flag)
    tp = cands[0] if len(cands) else 0.
    end = len(ys) - period
    while tp < end:
        lo = np.searchsorted(cands, tp - win); hi = np.searchsorted(cands, tp + win, 'right')
        if hi > lo:
            c = cands[lo + int(np.argmin(abs(cands[lo:hi] - tp)))]
            tp += gain * (c - tp) if locked else (c - tp)
            hits += 1; history.append(1)
        else:
            history.append(0); hits = 0
            if not locked:
                # Acquisition: re-seed on the next candidate before the next slot.
                nxt = np.searchsorted(cands, tp + win, 'right')
                if nxt < len(cands) and cands[nxt] < tp + period - win:
                    tp = cands[nxt] - period
        history = history[-32:]
        if not locked and hits >= 8: locked = True
        elif locked and len(history) == 32 and sum(history) < 16: locked = False
        line_start.append((tp, locked))
        tp += period
    ls = np.array([p for p, _ in line_start]); lk = np.array([l for _, l in line_start], bool)
    # Compare with true line starts (horizontal syncs + those inside the V
    # interval at full-line spacing are approximated by the period grid).
    if len(hs) == 0 or len(ls) == 0:
        return dict(h_ok=0., locked=0., false_per_line=0., missed_per_line=1., v_ok=0.)
    # A constant decode/slicer delay is invisible on a goggle: remove the
    # circular median offset, then judge each line's position error.
    grid0 = hs[0] % period
    d = (ls - grid0 + period / 2) % period - period / 2
    if lk.any():
        ang = np.angle(np.mean(np.exp(2j * np.pi * d[lk] / period))) * period / (2 * np.pi)
        d = (d - ang + period / 2) % period - period / 2
    ok = lk & (np.abs(d) < 20)
    # Raw candidate statistics against all true sync-like pulses.
    if len(cands):
        j = np.clip(np.searchsorted(all_s, cands), 0, len(all_s) - 1); jp = np.maximum(j - 1, 0)
        false = int(np.sum(np.minimum(abs(all_s[j] - cands), abs(all_s[jp] - cands)) > 40))
        jj = np.clip(np.searchsorted(cands, hs), 0, len(cands) - 1); jjp = np.maximum(jj - 1, 0)
        missed = int(np.sum(np.minimum(abs(cands[jj] - hs), abs(cands[jjp] - hs)) > 40))
    else:
        false, missed = 0, len(hs)
    # Vertical: a broad pulse train is detected when the sync-low fraction over
    # 20 us exceeds 70 % within +-3 lines of the true V start.
    lowf = np.convolve(below, np.ones(800) / 800, 'same')
    vstarts = vs[np.r_[True, np.diff(vs) > 8000]] if len(vs) else vs
    v_ok = [bool(np.any(lowf[max(0, v - int(3 * period)):v + int(3 * period)] > .7)) for v in vstarts]
    burn = 40  # ignore the first lines (initial acquisition)
    return dict(h_ok=float(np.mean(ok[burn:])) if len(ok) > burn else 0.,
                locked=float(np.mean(lk[burn:])) if len(lk) > burn else 0.,
                false_per_line=false / max(len(hs), 1), missed_per_line=missed / max(len(hs), 1),
                v_ok=float(np.mean(v_ok)) if v_ok else 0.)


def luma_sinad(y_ire, truth, region):
    m = region == 1
    e = y_ire[m] - truth[m]
    return float(10 * np.log10(np.var(truth[m]) / max(np.mean(e ** 2), 1e-12)))


def measure(y, y_clean, c):
    cal = calibrate(y_clean, c['truth'], c['region'])
    yi = apply(y, cal)
    r = goggle(yi, c['truth'], c['standard'])
    rp = goggle(yi, c['truth'], c['standard'], agc='peak')
    r.update({'peak_' + k: v for k, v in rp.items()})
    r['sinad'] = luma_sinad(yi[3000:-3000], c['truth'][3000:-3000], c['region'][3000:-3000])
    return r
