#!/usr/bin/env python3
"""C5VRX final video fit with explicit clean DAC-transfer constraints.
Training levels are deterministic; selection 591/592, final 791/792/793.
"""
import argparse,json
from pathlib import Path
import numpy as np
import optimize_pair as P
import optimize_video as Q


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    old=json.loads((P.ROOT/'tools/vlp56_codebook.json').read_text());old.update(name='VLP56',current_tokens=56,previous_tokens=28,stride=64,previous_shift=1)
    models=[old]
    for weight in (100,1000):
        for reg in (.1,):
            m=Q.train_video(old,'balanced',reg,weight);m['name']=f'OVP56-level{weight}-reg{reg}';models.append(m);print(m['name'],m['fit'],flush=True)
    selection=Q.evaluate(models,(591,592));sm=P.summary(selection)
    def eligible(m):
        if m['name']=='VLP56':return True
        if m['fit']['max_level_error_dac']>1:return False
        for kind in ('random','bars','multitone'):
            values=[r['sinad'] for r in selection if r['model']==m['name'] and r['cnr']==14 and r['kind']==kind]
            refs=[r['sinad'] for r in selection if r['model']=='VLP56' and r['cnr']==14 and r['kind']==kind]
            if np.mean(values)<np.mean(refs)-.8:return False
        return True
    def obj(m):return np.mean([sm[m['name']][str(c)][0] for c in (0,2,4,6)])
    rank=sorted([m for m in models if eligible(m)],key=obj,reverse=True);win=rank[0]
    print('RANK',[(m['name'],obj(m)) for m in rank],flush=True)
    for m in models:(a.output/(m['name']+'.json')).write_text(json.dumps(m,indent=2)+'\n')
    win=dict(win);win.update(selection_seeds=[591,592],final_seeds=[791,792,793],selection_objective='weak SINAD 0/2/4/6; each 14-dB scenario within .8 dB; clean absolute DAC level error <=1 code across 768 level/radius/CFO conditions')
    (a.output/'winner.json').write_text(json.dumps(win,indent=2)+'\n')
    (a.output/'selection.json').write_text(json.dumps(selection,indent=2)+'\n')
    final=Q.evaluate([win,old],(791,792,793),full=True)
    for seed in (791,792,793):
        for cfo,dev,kind,amp,dc,skew in [(1e6,6.7e6,'random',1,0j,1),(.25e6,5.5e6,'bars',.6,.2-.3j,1.1),(2e6,8e6,'multitone',1.5,0j,.9)]:
            sig,noise,_,ire=P.stream(seed,n=131072,cfo=cfo,deviation=dev,kind=kind);P.D.truth=P.D.goggle(ire)
            for cnr in P.CNRS:
                raw=P.raw_at(sig,noise,cnr,amp,dc,skew)
                for name,fn in [('HC50',P.V.H.DESIGNS['hc50p6']),('adjacent40',P.D.DESIGNS['adj40'])]:
                    tot,bands,clicks=P.D.score(fn(raw));final.append(dict(seed=seed,cfo=cfo,deviation=dev,kind=kind,cnr=cnr,model=name,sinad=float(tot),clicks=float(clicks),bands=list(map(float,bands))))
    (a.output/'final.json').write_text(json.dumps(final,indent=2)+'\n')
    summary=dict(winner=win['name'],selection=sm,final=P.summary(final),seed_policy='421/422 filtered fit plus deterministic clean levels;591/592 selection;791/792/793 final, no reselection')
    (a.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('FINAL',json.dumps(summary['final']),flush=True)

if __name__=='__main__':main()
