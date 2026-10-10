#!/usr/bin/env python3
"""Full-field picture-cliff study on the board lane (C5VRX by Twotoz/contributors).

Runs every pinned demod on identical full PAL/NTSC fields (fixed ultrafine
lane, AFC-centred carrier, randomized VTX deviation/DC/IQ) and reports the
goggle-lock model of goggle_lock.py next to luma SINAD, per C/N.
"""
import argparse
import json
import multiprocessing as mp
import os
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
STATE = {}


def make(seed, cnr, std, dev, rms, pattern, lane='ultrafine', extra=None):
    import waveforms as V
    rng = np.random.default_rng(seed)
    cfo = -436e3 + dev * 1436e3 + float(rng.uniform(-5e4, 5e4))
    board = os.environ.get('CLIFF_BOARD') == '1'
    if board:  # AFC/AutoFit never fitted on the board: off-centre carrier, nominal fit
        cfo += float(rng.uniform(-1e6, 1e6))
    hw = dict(deviation=dev, dc=complex(*rng.uniform(-.15, .15, 2)),
              iq_gain=float(rng.uniform(.97, 1.03)), iq_phase_deg=float(rng.uniform(-2, 2)))
    hw.update(extra or {})
    if os.environ.get('CLIFF_STRESS') == '1':
        # Flight dynamics: V5 hunting (+-1 dB steps every ~10 ms), two
        # 50-300 us carrier fades per field, and two multipath phase jumps.
        dur = 40000. if std == 'PAL' else 33367.
        t = 0.; gw = []
        while t < dur - 2000:
            t1 = t + float(rng.uniform(3000, 15000)); g = float(10 ** (rng.choice([-1, 1]) / 20))
            gw.append((t, min(t1, dur - 1), g)); t = t1
        hw['gain_windows'] = tuple(gw)
        lw = []
        for k in range(4):
            s0 = float(rng.uniform(1000, dur - 1500)); lw.append((s0, s0 + float(rng.uniform(50, 300))))
        hw['loss_windows_us'] = tuple(sorted(lw))
        hw['phase_jumps'] = tuple((float(rng.uniform(1000, dur - 1000)), float(rng.uniform(-np.pi, np.pi))) for _ in range(2))
    c = V.make_case(std, seed, cnr, rms, short=False, cfo_hz=cfo, stimulus_seed=seed + 1,
                    lane_model=lane, pattern=pattern, include_traces=os.environ.get('CLIFF_ANALOG') == '1', **hw)
    c.pop('rx_signal', None)
    c['dev'] = dev
    c['fit'] = dict(fit_deviation=dev * float(rng.uniform(.95, 1.05)), fit_centre_hz=cfo + float(rng.uniform(-5e4, 5e4)))
    if board: c['fit'] = dict(fit_deviation=1.0, fit_centre_hz=1e6)
    return c


def demods():
    import edge_fsm as F
    from pair_autofit import remap
    opts = json.loads((ROOT / 'tools/range_options.json').read_text())['options']
    pair = next(o for o in opts if o['label'] == 'PAIR RANGE LAB')['model']
    edge = next(o for o in opts if o['label'] == 'EDGE RANGE LAB')['model']['params']
    r32 = json.loads((ROOT / 'tools/range32_model.json').read_text())
    return dict(
        HR50=lambda c: ('hr50', None),
        RANGE32=lambda c: ('lut', r32),
        PAIR_AF=lambda c: ('lut', remap(pair, c['fit']['fit_deviation'], c['fit']['fit_centre_hz'])),
        EDGE_AF=lambda c: ('lut', F.synthesize(dict(edge, **c['fit']))),
        HR50_AF=lambda c: ('hr50af', dict(c['fit'], lo=-48., hi=120.)),
    )


def hr50_phase():
    import bs_model as BS
    text = (ROOT / 'firmware/programs/c5vrx4_reference_phase8_hr.bsasm').read_text()
    return np.array([v >> 8 for v in BS.parse(text)[1][:256]], np.int64)


def hr50af_codes(m):
    """Analytic span50 transfer: signed Phase8 difference d (256/turn per
    50 ns) -> IRE through the AutoFit deviation/centre, clipped to the legal
    CVBS range [lo, hi] and spread over all 64 physical DAC levels."""
    import overlay_fsm as O
    V = O.H.B.DAC_VOLTS
    d = np.arange(256) - 128                       # (128 + c - p) & 255, signed
    f = d / 256 * 20e6                              # Hz
    ire = (f - m['fit_centre_hz']) / (m['fit_deviation'] * 6.7e6 / 140) + 30
    ire = np.clip(ire, m['lo'], m['hi'])
    target = (ire - m['lo']) / (m['hi'] - m['lo']) * V[-1]
    return np.argmin(abs(target[:, None] - V[None, :]), axis=1)


def hr50_decode(raw, table=None):
    import overlay_fsm as O
    B = O.H.B
    text = (ROOT / 'firmware/programs/c5vrx4_reference_phase8_hr.bsasm').read_text()
    import bs_model as BS
    lut = BS.parse(text)[1]
    ph = np.array([v >> 8 for v in lut[:256]], np.int64)
    p = ph[raw[1::2].astype(np.int64)]
    codes = ((128 + p[1:] - p[:-1]) & 255)
    codes = codes >> 2 if table is None else table[codes]
    out = np.zeros(len(raw), np.int64)
    out[2:2 + 2 * len(codes):2] = codes; out[3:3 + 2 * len(codes):2] = codes
    return B.D.goggle(B.DAC_VOLTS[out])


def dual_decode(raw, mode):
    """Memoryless span50 variants using both IQ40 samples of each span.
    avg: mean of the a-chain and b-chain Phase8 differences (each wrapped);
    unwrap: same, but each 50-ns difference is the sum of two 25-ns wraps."""
    import overlay_fsm as O
    B = O.H.B; ph = hr50_phase()
    pa = ph[raw[0::2].astype(np.int64)]; pb = ph[raw[1::2].astype(np.int64)]
    n = min(len(pa), len(pb))
    pa, pb = pa[:n], pb[:n]
    w = lambda x: ((x + 128) & 255) - 128
    if mode == 'avg':
        d = (w(pa[1:] - pa[:-1]) + w(pb[1:] - pb[:-1])) / 2
    elif mode == 'unwrap':
        da = w(pb[:-1] - pa[:-1]) + w(pa[1:] - pb[:-1])          # a_k -> a_k+1 via b_k
        db = w(pa[1:] - pb[:-1]) + w(pb[1:] - pa[1:])            # b_k -> b_k+1 via a_k+1
        d = np.clip((da + db) / 2, -128, 127)
    else:
        raise ValueError(mode)
    codes = np.clip(np.floor((128 + d) / 4), 0, 63).astype(np.int64)
    out = np.zeros(len(raw), np.int64)
    out[2:2 + 2 * len(codes):2] = codes; out[3:3 + 2 * len(codes):2] = codes
    return B.D.goggle(B.DAC_VOLTS[out])


def cells(raw):
    i = (raw.astype(np.int64) >> 4) & 15; q = raw.astype(np.int64) & 15
    i = np.where(i > 7, i - 16, i) + .5; q = np.where(q > 7, q - 16, q) + .5
    return i + 1j * q


def pq_decode(raw, gamma, bits6):
    """Stateless span50 whose endpoint phase also sees the quadrant of the
    span's first IQ40 sample: z = b + gamma * unit(quadrant(a)); phase of z
    (6 or 8 bits), then (128 + c - p) >> 2 as in HR50."""
    import overlay_fsm as O
    B = O.H.B
    a = cells(raw[0::2]); b = cells(raw[1::2]); n = min(len(a), len(b)); a, b = a[:n], b[:n]
    qa = (np.sign(a.real) + 1j * np.sign(a.imag)) / np.sqrt(2)
    z = b + gamma * qa
    p = np.floor((np.angle(z) / (2 * np.pi)) * 256).astype(np.int64) & 255
    if bits6: p &= ~3
    codes = ((128 + p[1:] - p[:-1]) & 255) >> 2
    out = np.zeros(len(raw), np.int64)
    out[2:2 + 2 * len(codes):2] = codes; out[3:3 + 2 * len(codes):2] = codes
    return B.D.goggle(B.DAC_VOLTS[out])


def rx5808_decode(y):
    """Analog VRX reference (RX5808-style limiter + quadrature discriminator):
    ideal phase difference of the unquantized IQ at 40 MS/s, no 4-bit lanes,
    no DAC quantization, then the same goggle input filter."""
    import overlay_fsm as O
    y = np.asarray(y, np.complex128)
    d = np.angle(y[1:] * np.conj(y[:-1]))
    return O.H.B.D.goggle(np.r_[d[:1], d])


def run(kind, model, raw):
    if kind == 'analog': return rx5808_decode(raw)
    if kind == 'pq': return pq_decode(raw, *model)
    if kind == 'dual': return dual_decode(raw, model)
    if kind == 'hc50':
        import weak_signal_sweep as W
        return W.decode(raw, dict(name='HC50'))
    import overlay_fsm as O
    if kind == 'hr50': return hr50_decode(raw)
    if kind == 'hr50af': return hr50_decode(raw, hr50af_codes(model))
    return O.decode(raw, model)


def setup():
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
    import sys
    sys.path.insert(0, str(ROOT / 'tools'))


def lookup(D, n):
    if n.startswith('PQ:'):
        _, g, b6 = n.split(':')
        return lambda c: ('pq', (float(g), b6 == '6'))
    if n == 'U85':
        m = json.loads((ROOT / 'tools/range_options.json').read_text())['options'][6]['model']
        return lambda c, m=m: ('lut', m)
    if n == 'RX5808':
        return lambda c: ('analog', None)
    if n.startswith('UP'):
        kp = float(n[2:] or 1) if n[2:] != '1' else 1.
        def mk(c, kp=kp):
            import untrained_pair as U
            return ('lut', U.analytic_pair(kp, c['fit']['fit_deviation'], c['fit']['fit_centre_hz']))
        return mk
    if n == 'HC50':
        return lambda c: ('hc50', None)
    if n in ('AVG', 'UNWRAP'):
        return lambda c: ('dual', n.lower())
    if n.startswith('AF:'):
        _, lo, hi = n.split(':')
        return lambda c: ('hr50af', dict(c['fit'], lo=float(lo), hi=float(hi)))
    return D[n]


def job(spec):
    import goggle_lock as G
    seed, cnr, std, dev, rms, pattern, names = spec
    c = make(seed, cnr, std, dev, rms, pattern)
    D = demods(); rows = []
    for n in names:
        kind, m = lookup(D, n)(c)
        y = run(kind, m, c['raw']); yc = run(kind, m, c['clean'])
        r = G.measure(y, yc, c)
        rows.append(dict(name=n, seed=seed, cnr=cnr, std=std, dev=dev, rms=rms, pattern=pattern, **r))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--seed', type=int, default=610000)
    ap.add_argument('--per', type=int, default=4)
    ap.add_argument('--cnr', default='0,2,4,6,8,10,12,14,17,20,25')
    ap.add_argument('--names', default='HR50,RANGE32,PAIR_AF,EDGE_AF')
    ap.add_argument('--workers', type=int, default=7)
    a = ap.parse_args()
    cnrs = [float(x) for x in a.cnr.split(',')]; names = a.names.split(',')
    rng = np.random.default_rng(a.seed); specs = []
    for cnr in cnrs:
        for k in range(a.per):
            std = ('PAL', 'NTSC')[k % 2]
            dev = (.69, 1.0, .6, 1.4, .8, 1.2)[k % 6] if k < 6 else float(rng.uniform(.6, 1.4))
            if os.environ.get('CLIFF_DEV'): dev = float(os.environ['CLIFF_DEV'])
            rms = float(rng.uniform(3.5, 5.))
            pattern = ('texture', 'bars', 'zoneplate', 'checker')[k % 4]
            specs.append((a.seed + 1000 * k + int(cnr * 10), cnr, std, dev, rms, pattern, names))
    rows = []
    with mp.get_context('spawn').Pool(a.workers, initializer=setup) as pool:
        for r in pool.imap_unordered(job, specs):
            rows.extend(r)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(rows, indent=0))
    print('C/N  ' + ' | '.join(f'{n:>26s}' for n in names))
    print('     ' + ' | '.join(f'{"hok peak miss false sinad":>26s}' for n in names))
    for cnr in cnrs:
        cells = []
        for n in names:
            rs = [r for r in rows if r['name'] == n and r['cnr'] == cnr]
            cells.append('%4.2f %4.2f %4.2f %5.2f %5.1f' % tuple(np.mean([r[k] for r in rs]) for k in
                                                         ('h_ok', 'peak_h_ok', 'missed_per_line', 'false_per_line', 'sinad')))
        print(f'{cnr:4.0f} ' + ' | '.join(f'{c:>26s}' for c in cells), flush=True)


if __name__ == '__main__':
    main()
