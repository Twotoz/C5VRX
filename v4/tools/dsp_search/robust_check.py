#!/usr/bin/env python3
"""Robustness of the untrained demods outside the search envelope
(C5VRX by Twotoz/contributors). Board conditions + flight stress, plus one
extra impairment per row: amplitude high enough to fold the ultrafine lanes,
large I/Q imbalance (board once read gain 1.42 / 22 deg), carrier far off
centre, a CW spur inside the channel (R8/E7 5920 MHz), echo + fade, and DC.
Picture = min(ideal, peak goggle) correctly displayed lines.
"""
import json
import multiprocessing as mp
import os
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
NAMES = os.environ.get('ROBUST_NAMES', 'U85E,U85,HC50,RANGE32,RX5808').split(',')
CONDITIONS = {
    'nominal': {},
    'rms6.5': dict(rms=6.5),
    'rms8': dict(rms=8.),
    'iq1.4/20deg': dict(iq_gain=1.4, iq_phase_deg=20.),
    'cfo+2MHz': dict(cfo_extra=2e6),
    'cfo-2MHz': dict(cfo_extra=-2e6),
    'cw+3.1MHz': dict(interferers=((.3, 3.1e6, 0.),)),
    'cw-5MHz': dict(interferers=((.3, -5e6, 0.),)),
    'echo+fade': dict(stress=True),
    'dc0.3': dict(dc=.3 - .2j),
}


def job(spec):
    os.environ['CLIFF_BOARD'] = '1'; os.environ['CLIFF_STRESS'] = '1'; os.environ['CLIFF_ANALOG'] = '1'
    sys.path.insert(0, str(ROOT / 'tools'))
    import cliff_study as S
    import goggle_lock as G
    cond, seed, cnr, dev = spec
    extra = dict(CONDITIONS[cond]); rms = extra.pop('rms', 4.2); cfo = extra.pop('cfo_extra', 0.)
    if cfo:
        extra['_cfo_extra'] = cfo
    std = ('PAL', 'NTSC')[seed % 2]
    c = S.make(seed, cnr, std, dev, rms, 'texture', extra=extra)
    rows = []
    D = S.demods()
    for n in NAMES:
        kind, m = S.lookup(D, n)(c)
        src, srcc = (c['analog'], c['analog_clean']) if kind == 'analog' else (c['raw'], c['clean'])
        r = G.measure(S.run(kind, m, src), S.run(kind, m, srcc), c)
        rows.append(dict(name=n, cond=cond, cnr=cnr, dev=dev, pic=min(r['h_ok'], r['peak_h_ok']), sinad=r['sinad']))
    return rows


def main():
    specs = [(cond, 950000 + 17 * i, cnr, dev) for i, (cond, cnr, dev) in enumerate(
        (c, n, d) for c in CONDITIONS for n in (6., 10., 20.) for d in (.69, 1.2))]
    with mp.get_context('spawn').Pool(7) as p:
        rows = [r for rr in p.map(job, specs) for r in rr]
    Path(sys.argv[1]).write_text(json.dumps(rows))
    print('condition      ' + ' '.join(f'{n:>16s}' for n in NAMES) + '   (picture / SINAD, mean of C/N 6,10,20 x dev .69,1.2)')
    for cond in CONDITIONS:
        cells = []
        for n in NAMES:
            rs = [r for r in rows if r['name'] == n and r['cond'] == cond]
            cells.append('%.2f/%5.1f' % (np.mean([r['pic'] for r in rs]), np.mean([r['sinad'] for r in rs])))
        print(f'{cond:14s} ' + ' '.join(f'{x:>16s}' for x in cells), flush=True)


if __name__ == '__main__':
    main()
