#!/usr/bin/env python3
"""Full-frame, frozen-hypothesis confirmation for C5VRX architecture search.

Selection 9401 is independent of 9301 proxies. One winner is frozen before
9501/9502 final and 9601 echo/fading tests. A veto never selects a runner-up.
Raw capture replay is observational when no known source waveform exists.
"""
import argparse
import csv
import json
from pathlib import Path
import numpy as np
from numba import njit
import engine as S
import hardware as H
import waveforms as V
import generate_pll96 as G
import demod_theories as T


@njit(cache=True)
def phase_loop(raw,lut):
    out=np.zeros(len(raw),np.uint8);state=0
    for k in range(len(raw)//2-1):
        token=lut[768+raw[2*k]]
        value=lut[state*8+token];state=value>>6
        out[2*k+2]=out[2*k+3]=value&63
    return out


def controls():
    old=np.array(H.BS.parse((G.ROOT/'tools/fixtures/pll96-rejected-84aadd1.bsasm').read_text())[1],np.int64)
    pin=json.loads((G.ROOT/'tools/fixtures/pll96-rejected-84aadd1.json').read_text())
    table,enc=G.tables(pin['params']);new=np.array(table+enc,np.int64)
    hypotheses=json.loads((G.ROOT/'tools/detector_study/models/theory_hypotheses.json').read_text())
    floating=next(m for m in hypotheses['winners'] if m['family']=='pll')
    return [('PLL96-rejected-board',lambda raw:H.B.D.goggle(H.B.DAC_VOLTS[phase_loop(raw,old)])),
            ('PLL96-IQ-corrected',lambda raw:H.B.D.goggle(H.B.DAC_VOLTS[phase_loop(raw,new)])),
            ('floating-PLL-IQ40',lambda raw:T.decode(raw,floating)),
            ('HC50',lambda raw:V.W.decode(raw,dict(name='HC50')))]


def evaluate(models,seed,cnrs,stage,stress=False):
    rows=[];baseline=S.F.load_reference('OVP56')
    functions=[(m['name'],lambda raw,m=m:S.decode(raw,m)) for m in models]+controls()
    for standard in ('PAL','NTSC'):
        for cnr in cnrs:
            c=V.make_case(standard,seed,cnr,3,stress=stress)
            refclean=S.decode(c['clean'],baseline)
            c['calibration']=V.M.clean_calibration(refclean[:131072],c['truth'][:131072],3000)
            assert c['calibration'][1]>0
            reference=V.waveform_metrics(S.decode(c['raw'],baseline),c)
            for name,fn in functions:
                y=fn(c['raw']);stats=V.waveform_metrics(y,c)
                stats.update(stage=stage,seed=seed,standard=standard,cnr=cnr,model=name,
                             gate_failures=';'.join(V.gate(stats,reference,cnr>=14)),
                             loaded_dac_min_volts=float(np.min(y)*3.3/(sum(1/r for r in (8200,3900,2000,1000,470,240))+1/200+1/75)),
                             loaded_dac_max_volts=float(np.max(y)*3.3/(sum(1/r for r in (8200,3900,2000,1000,470,240))+1/200+1/75)))
                rows.append(stats)
            print('FRAME',stage,standard,cnr,seed,'models',len(functions),flush=True)
    return rows


def aggregate(rows):
    result={}
    for name in sorted({r['model'] for r in rows}):
        rr=[r for r in rows if r['model']==name];weak=[r for r in rr if r['cnr']<=6]
        result[name]=dict(eligible=all(not r['gate_failures'] for r in rr),
                         weak_sinad=float(np.mean([r['sinad'] for r in weak])),
                         weak_h_missing=int(sum(r['h_missing'] for r in weak)),
                         strong_h_missing=int(sum(r['h_missing'] for r in rr if r['cnr']>=14)),
                         failures=sorted({x for r in rr for x in r['gate_failures'].split(';') if x}))
    return result


def write_rows(path,rows):
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--search',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--captures',type=Path)
    a=ap.parse_args()
    if a.output.exists():ap.error('use a fresh output directory')
    a.output.mkdir(parents=True)
    frozen=json.loads((a.search/'frozen.json').read_text())
    baseline=S.F.load_reference('OVP56');vlp=S.F.load_reference('VLP56')
    models=[baseline,vlp]+frozen['finalists']
    rows=evaluate(models,9401,(2,6,30),'selection')
    write_rows(a.output/'selection.csv',rows);summary=aggregate(rows)
    eligible=[m for m in frozen['finalists'] if summary[m['name']]['eligible'] and
              summary[m['name']]['weak_sinad']>summary['OVP56']['weak_sinad']]
    winner=max(eligible,key=lambda m:summary[m['name']]['weak_sinad']) if eligible else baseline
    S.save(a.output/'frozen_winner.json',dict(winner=winner,selection=summary,no_reselection=True,
            policy='baseline unless all waveform/sync/strong gates pass; independent finals can veto'))
    final=[]
    for seed in (9501,9502):final+=evaluate([baseline,vlp,winner] if winner['name']!='OVP56' else [baseline,vlp],seed,(0,4,30),'final')
    write_rows(a.output/'final.csv',final)
    channel=evaluate([baseline,vlp,winner] if winner['name']!='OVP56' else [baseline,vlp],9601,(2,6,30),'echo_fade',True)
    write_rows(a.output/'channel.csv',channel)
    fs,cs=aggregate(final),aggregate(channel)
    accepted=(winner['name']!='OVP56' and fs[winner['name']]['eligible'] and cs[winner['name']]['eligible'] and
              fs[winner['name']]['weak_sinad']>fs['OVP56']['weak_sinad'] and
              cs[winner['name']]['weak_sinad']>cs['OVP56']['weak_sinad'])
    result=dict(selected=winner['name'],confirmed=accepted,final=fs,echo_fade=cs,
                decision='research candidate only; board validation still required' if accepted else 'retain OVP56; no new architecture promoted',
                captures='none supplied; no physical improvement claim')
    if accepted:
        S.save(a.output/'confirmed_model.json',winner)
        (a.output/'confirmed.bsasm').write_text(H.compile_model(winner))
    if a.captures:
        observed=[]
        for path in a.captures.glob('*.raw'):
            raw=np.frombuffer(path.read_bytes(),np.uint8)
            if len(raw)%2 or len(raw)<8192:raise ValueError('capture needs contiguous, even Q4/I4 bytes')
            for model in [baseline,winner]:
                y=S.decode(raw,model);np.save(a.output/(path.stem+'-'+model['name']+'.npy'),y)
                observed.append(dict(capture=path.name,model=model['name'],samples=len(raw),scope='unknown truth; no SINAD/range score'))
        result['captures']=observed
    S.save(a.output/'summary.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':main()
