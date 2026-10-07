#!/usr/bin/env python3
"""Independent hardware-fit PLL96 experiment for C5VRX/Twotoz/contributors.

Coarse LUT16 pair20 PLL, not the floating IQ40 winner. Select on weak quality;
record strong regressions explicitly. User-requested lab mode, not a default.
"""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
from numba import njit
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate_pll96 as G
import mega_demod_bench as M


@njit(cache=True)
def codes(raw, lut, encoder):
    state = 0
    result = np.zeros(len(raw), dtype=np.int64)
    for k in range(0, len(raw), 2):
        value = lut[state*8+encoder[raw[k]]]
        state = value >> 6
        result[k] = result[k+1] = value & 63
    return result


def decode(raw, p):
    lut, encoder = G.tables(p)
    return M.D.goggle(M.F.B.DAC_VOLTS[codes(raw, np.array(lut), np.array(encoder))])


def measure(p, c, short=False):
    sl, burn = (slice(4096, 5120), 128) if short else (slice(None), 3000)
    y, clean = decode(c['raw'][sl], p), decode(c['clean'][sl], p)
    truth = c['truth'][sl]
    cal = M.clean_calibration(clean, truth, burn)
    return dict(model='PLL96', case_id=c.get('case_id', f"channel/{c['seed']}/{c['kind']}/{c['cnr']}/{c['rms']}"), seed=c['seed'], kind=c['kind'],
                cnr=c['cnr'], rms=c['rms'], **M.metrics(y, truth, cal, burn))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--configurations', type=int, default=8192)
    ap.add_argument('--confirm', type=Path, help='veto-only confirmation of a previously frozen model')
    a = ap.parse_args()
    if a.output.exists():
        ap.error('use a fresh output directory')
    a.output.mkdir(parents=True)
    if a.confirm:
        frozen = json.loads(a.confirm.read_text())
        M.S.write_json(a.output/'protocol.json', dict(seeds=[8401,8402,8403],channel=[8501,8502,8503],
                      model=frozen,policy='fresh veto after architecture comparison; no reselection'))
        for stage, cases in [('final', M.F.dataset([8401,8402,8403], (0,2,4,6,14,18,30),
                                                  (.75,1.5,3,5), n=131072)),
                             ('channel', M.channel_cases([8501,8502,8503]))]:
            rows = [measure(frozen['params'],c) for c in cases]
            for name in ('OVP56','VLP56'):
                rows.extend(M.measure(M.F.load_reference(name),c) for c in cases)
            M.S.write_rows(a.output/(stage+'.csv'),rows)
            M.S.write_json(a.output/(stage+'_summary.json'), M.summarize(rows,True))
        return
    M.S.write_json(a.output/'protocol.json', dict(configuration_seed=7201, screen_seed=7202,
                  selection=[7301,7302], final=[7401,7402,7403],
                  policy='weak-objective selection; strong regressions reported, no production promotion'))
    assert G.tables([.5,.2,.5,-3e6,5e6,0])[0]
    assert M.F.P.OFFSET == -46.08 and abs(M.F.P.SCALE-63/117.76) < 1e-12
    cases = [M.F.case(7202,kind,cnr,rms) for kind,cnr,rms in
             [('random',2,1.5),('bars',4,3),('multitone',6,5)]]
    rng = np.random.default_rng(7201)
    leaders, screen = [], []
    for j in range(a.configurations):
        p = [rng.uniform(.1,1.6),10**rng.uniform(-2, .2),rng.uniform(0,1.5),
             rng.uniform(-5e6,-1e6),rng.uniform(3e6,7e6),rng.uniform(0,np.pi/4),
             (2,4,8,16,32)[j%5]]
        value = M.objective([measure(p,c,True) for c in cases])
        screen.append(dict(index=j,objective=value,params=json.dumps(p)))
        leaders.append((value,p));leaders.sort(reverse=True);del leaders[16:]
        if (j+1)%512 == 0:print('PLL96 SCREEN',j+1,flush=True)
    M.S.write_rows(a.output/'screen.csv',screen)
    selection = M.F.dataset([7301,7302], (0,2,4,6,14,18), (1.5,3))
    best, rows = None, []
    for j,(_,p) in enumerate(leaders):
        rr = [dict(measure(p,c),model=f'PLL96-{j}') for c in selection]
        rows.extend(rr)
        value = M.objective([r for r in rr if r['cnr']<=6])
        if best is None or value > best[0]:best=(value,p)
    M.S.write_rows(a.output/'selection.csv',rows)
    frozen = dict(name='PLL96',params=best[1],selection_weak_objective=best[0],
                  scope='96 phase/frequency states, LUT16 pair20 PLL; experimental, not IQ40 PLL',
                  provenance='C5VRX by Twotoz and contributors; existing PLL research; https://github.com/Twotoz/C5VRX; https://twotoz.github.io/C5VRX/')
    M.S.write_json(a.output/'frozen.json',frozen)
    final = M.F.dataset([7401,7402,7403], (0,2,4,6,14,18,30), (.75,1.5,3,5),n=131072)
    rows=[measure(best[1],c) for c in final]
    for name in ('OVP56','VLP56'):
        ref=M.F.load_reference(name)
        rows.extend(M.measure(ref,c) for c in final)
    M.S.write_rows(a.output/'final.csv',rows)
    summary=M.summarize(rows,True)
    M.S.write_json(a.output/'summary.json',summary)
    print('PLL96 RESULT',json.dumps(summary),flush=True)


if __name__ == '__main__':main()
