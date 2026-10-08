#!/usr/bin/env python3
"""Freeze a deployable winner using selection-only quality guards, then test.

Selection 561/562, final 761/762/763. Negative prior rounds are recorded rather
than discarded. No final-seed reselection. Candidate manifests stay unchanged.
"""
import argparse,json
from pathlib import Path
import numpy as np
import optimize_pair as P
import optimize_video as Q


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--candidates',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    old=json.loads((P.ROOT/'tools/vlp56_codebook.json').read_text());old.update(name='VLP56',current_tokens=56,previous_tokens=28,stride=64,previous_shift=1)
    # Frozen source candidates, not refitted against selection or final signals.
    models=[old]+[json.loads(p.read_text()) for p in sorted(a.candidates.glob('*video*.json'))]
    selected=Q.evaluate(models,(561,562));summary=P.summary(selected)
    ref=[r for r in selected if r['model']=='VLP56'];eligible=[];guards={}
    for m in models:
        name=m['name'];rs=[r for r in selected if r['model']==name]
        # Apply per scenario, so aggregate gains cannot hide a strong-detail failure.
        strong=[]
        for kind in ('random','bars','multitone'):
            base=np.mean([r['sinad'] for r in ref if r['kind']==kind and r['cnr']==14])
            value=np.mean([r['sinad'] for r in rs if r['kind']==kind and r['cnr']==14])
            strong.append(float(value-base))
        guards[name]=dict(strong_sinad_deltas=strong,passed=min(strong)>=-.8)
        if min(strong)>=-.8:eligible.append(m)
    def obj(m):return np.mean([summary[m['name']][str(c)][0] for c in (0,2,4,6)])
    winner=max(eligible,key=obj);print('GUARDS',json.dumps(guards),flush=True);print('WINNER',winner['name'],flush=True)
    winner=dict(winner);winner.update(selection_seeds=[561,562],final_seeds=[761,762,763],selection_objective='mean SINAD 0/2/4/6 dB across three scenarios; every 14-dB scenario within 0.8 dB of VLP56',quality_guards=guards[winner['name']])
    (a.output/'winner.json').write_text(json.dumps(winner,indent=2)+'\n')
    (a.output/'selection.json').write_text(json.dumps(selected,indent=2)+'\n')
    final=Q.evaluate([winner,old],(761,762,763),full=True)
    # Same independent stream and scoring for HC50 and every-sample adjacent FM.
    for seed in (761,762,763):
        for cfo,dev,kind,amp,dc,skew in [(1e6,6.7e6,'random',1,0j,1),(.25e6,5.5e6,'bars',.6,.2-.3j,1.1),(2e6,8e6,'multitone',1.5,0j,.9)]:
            sig,noise,_,ire=P.stream(seed,n=131072,cfo=cfo,deviation=dev,kind=kind);P.D.truth=P.D.goggle(ire)
            for cnr in P.CNRS:
                raw=P.raw_at(sig,noise,cnr,amp,dc,skew)
                for name,fn in [('HC50',P.V.H.DESIGNS['hc50p6']),('adjacent40',P.D.DESIGNS['adj40'])]:
                    tot,bands,clicks=P.D.score(fn(raw));final.append(dict(seed=seed,cfo=cfo,deviation=dev,kind=kind,cnr=cnr,model=name,sinad=float(tot),clicks=float(clicks),bands=list(map(float,bands))))
    (a.output/'final.json').write_text(json.dumps(final,indent=2)+'\n')
    out=dict(winner=winner['name'],guards=guards,selection=summary,final=P.summary(final),seed_policy='561/562 selection; 761/762/763 frozen final; no reselection',model_scope='synthetic Q4/I4, AWGN through existing 10 MHz channel, random/bars/multitone, 0.25-2 MHz CFO, 5.5-8 MHz deviation, gain/DC/skew stress; not full PAL/NTSC or hardware')
    (a.output/'summary.json').write_text(json.dumps(out,indent=2)+'\n');print('FINAL',json.dumps(out['final']),flush=True)

if __name__=='__main__':main()
