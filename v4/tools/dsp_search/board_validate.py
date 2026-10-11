#!/usr/bin/env python3
"""Wait for real video on the board, then validate the untrained demods on it
(C5VRX by Twotoz/contributors).

Polls an IQ snapshot every --period seconds. Video is present when the U85Q
decode shows line periodicity (autocorrelation at one PAL/NTSC line > 0.25).
Then it captures --count snapshots plus the status lines, and reports per demod:
- clicks per 1000 samples (11-sample median deviation > 25 % of the span);
- the carrier's sync/porch/white frequencies from the measured DAC histogram
  of HC50 (linear, full +-10 MHz), i.e. whether U85Q/U85E windows hold them.
Writes report.json in --out. Board evidence, not range acceptance.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def snap(port, path):
    p = subprocess.run([sys.executable, str(ROOT / 'tools/capture_iq_snapshot.py'), port, str(path)],
                       capture_output=True, text=True)
    return path.exists() and 'Saved' in (p.stdout + p.stderr)


def periodicity(raw, m):
    import overlay_fsm as O
    y = O.model_codes(raw, m).astype(float)[8:]; y -= y.mean()
    ac = lambda L: float(np.dot(y[:-L], y[L:]) / max(np.dot(y, y), 1e-9))
    return max(ac(2560), ac(2542))


def clicks(y):
    y = y[400:]; tip = np.mean(np.sort(y)[:int(.05 * len(y))]); span = np.percentile(y, 99) - tip
    pad = np.pad(y, (5, 5), mode='edge')
    med = np.median(np.lib.stride_tricks.sliding_window_view(pad, 11), axis=1)[:len(y)]
    return float(np.mean(abs(y - med) > .25 * span) * 1000)


def status(port, seconds=8):
    import serial
    s = serial.Serial(); s.port, s.baudrate, s.timeout = port, 115200, .1; s.dtr = s.rts = False; s.open()
    d = bytearray(); s.write(b'T'); end = time.time() + seconds
    while time.time() < end: d.extend(s.read(4096))
    s.close()
    return [l for l in d.decode('utf-8', 'replace').splitlines()
            if l.startswith(('RXSUP', 'AUTOFIT', 'PREDEMOD_BW', 'C5VRX4 pipeline'))]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port', default='COM10'); ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--period', type=float, default=60.); ap.add_argument('--count', type=int, default=12)
    ap.add_argument('--max-wait', type=float, default=3300.)
    a = ap.parse_args(); a.out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / 'tools'))
    import overlay_fsm as O
    opts = json.loads((ROOT / 'tools/range_options.json').read_text())['options']
    models = {o['label']: o['model'] for o in opts if o['label'] in ('U85Q UNTRAINED', 'U85E UNTRAINED')}
    t0 = time.time(); probe = a.out / 'probe.bin'
    while True:
        if probe.exists(): probe.unlink()
        if snap(a.port, probe):
            p = periodicity(np.fromfile(probe, np.uint8), models['U85Q UNTRAINED'])
            print(f'probe t={time.time() - t0:.0f}s periodicity={p:.2f}', flush=True)
            if p > .25: break
        if time.time() - t0 > a.max_wait:
            print('NO_VIDEO timeout', flush=True); return
        time.sleep(a.period)
    print('VIDEO detected, capturing', flush=True)
    caps = []
    for k in range(a.count):
        f = a.out / f'iq-{k + 1}.bin'
        if snap(a.port, f): caps.append(np.fromfile(f, np.uint8))
    lines = status(a.port)
    (a.out / 'status.log').write_text('\n'.join(lines), encoding='utf-8')
    import bs_model as BS
    hr = (ROOT / 'firmware/programs/c5vrx4_reference_phase8_hr.bsasm').read_text()
    ph = np.array([v >> 8 for v in BS.parse(hr)[1][:256]], np.int64)
    def hr50_hz(raw):
        p = ph[raw[1::2].astype(np.int64)]
        d = ((p[1:] - p[:-1] + 128) & 255) - 128
        return d / 256 * 20e6
    f = np.concatenate([hr50_hz(r) for r in caps])
    # Sync tip = 2nd percentile, porch ~ mode of the lower half, white = 98th percentile (with noise).
    est = dict(sync_hz=float(np.percentile(f, 2)), white_hz=float(np.percentile(f, 98)),
               median_hz=float(np.median(f)))
    rep = dict(captures=len(caps), carrier_estimate=est, status=lines, demods={})
    for name, m in models.items():
        lo, hi = m['formula']['params']['range']
        rep['demods'][name] = dict(window_mhz=[lo, hi],
                                   clicks=float(np.mean([clicks(O.decode(r, m)) for r in caps])),
                                   sync_inside_window=bool(est['sync_hz'] > lo * 1e6),
                                   white_inside_window=bool(est['white_hz'] < hi * 1e6))
    import weak_signal_sweep as W
    rep['demods']['HC50'] = dict(clicks=float(np.mean([clicks(W.decode(r, dict(name='HC50'))) for r in caps])))
    (a.out / 'report.json').write_text(json.dumps(rep, indent=1))
    print('REPORT', json.dumps(rep), flush=True)


if __name__ == '__main__':
    main()
