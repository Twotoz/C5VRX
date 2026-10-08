#!/usr/bin/env python3
"""Direct LUT-entry polish for C5VRX range finalists (Twotoz/contributors).

Analytical and fitted finalists are limited by their formula families. This
stage edits individual executable words: DAC6 code +-1, next phase +-1 and
decoder token angle +-1, visiting the most-used entries first. A move is kept
only if the mean safe_range score on three training screens improves by a
margin; every 400 evaluations three separate held-out screens must not
decline, otherwise the last checkpoint is restored and polishing stops. The result is still only a candidate for validate_range.py.
"""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import time
import numpy as np


def objective(m,sets,R):
    scores=[]
    for cases in sets:
        r=R.score(m,cases,'safe_range')
        if r is None:return None
        scores.append(r['score'])
    return float(np.mean(scores))


def usage(m,sets,O,P):
    p=m['params'];b=p['token_bits'];transitions=np.zeros(1024);decoder=np.zeros(1024)
    for cases in sets:
        for c in cases:
            transitions+=np.bincount(O.model_indices(c['raw'],m),minlength=1024)
            if p.get('pair_layout'):decoder+=np.bincount(P.addresses(c['raw'],p['pair_layout']),minlength=1024)
            else:decoder[768:]+=np.bincount(c['raw'][0:-2:2],minlength=256)
    return transitions,decoder


def moves(m,transitions,decoder,minimum):
    order=[(c,'t',a) for a,c in enumerate(transitions) if c>=minimum]
    order+=[(c,'d',a) for a,c in enumerate(decoder) if c>=minimum]
    for _,kind,a in sorted(order,reverse=True):
        if kind=='t':
            for d in (1,-1):yield a,'dac',d
            for d in (1,-1):yield a,'phase',d
        else:
            for d in (1,-1):yield a,'token',d


def apply(lut,a,kind,d,p):
    b=p['token_bits'];sb=10-b;mask=(1<<sb)-1;phases=p['phases'];obs=(1<<b)//p['confidence_groups']
    w=int(lut[a])
    if kind=='dac':
        code=(w&63)+d
        if not 0<=code<=63:return None
        return (w&~63)|code
    if kind=='phase':
        state=(w>>6)&mask;state=state//phases*phases+(state%phases+d)%phases
        return (w&~(mask<<6))|(state<<6)
    token=w>>(16-b);token=token//obs*obs+(token%obs+d)%obs
    return (w&((1<<(16-b))-1))|(token<<(16-b))


def polish(args):
    entry,seeds,cap,minimum=args;margin,check=.02,400
    os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
    import search_range as R
    import overlay_fsm as O
    import pair_decoder as P
    import refine_overlay as F
    m=dict(entry.get('model') or O.synthesize(entry['params']));p=m['params']
    half=len(seeds)//2
    train=[R.cases(s,'fine') for s in seeds[:half]];held=[R.cases(s,'fine') for s in seeds[half:]]
    best=objective(m,train,R);held_best=objective(m,held,R)
    if best is None or held_best is None:return dict(id=entry['id'],status='base_ineligible')
    start=dict(train=best,held=held_best);evaluations=kept=pending=passes=0
    checkpoint=(list(m['lut']),best);stop=False
    while evaluations<cap and not stop:
        passes+=1;gained=0
        transitions,decoder=usage(m,train,O,P)
        for a,kind,d in moves(m,transitions,decoder,minimum):
            if evaluations>=cap:break
            word=apply(m['lut'],a,kind,d,p)
            if word is None:continue
            trial=dict(m,lut=list(m['lut']));trial['lut'][a]=word;evaluations+=1
            value=objective(trial,train,R)
            # A margin keeps single noisy screen events from steering edits.
            if value is not None and value>best+margin:m=trial;best=value;pending+=1;gained+=1
            if evaluations%check==0 and pending:
                value=objective(m,held,R)
                if value is None or value<held_best:
                    m=dict(m,lut=checkpoint[0]);best=checkpoint[1];stop=True;break
                held_best=value;kept+=pending;pending=0;checkpoint=(list(m['lut']),best)
        if not stop and pending:
            value=objective(m,held,R)
            if value is None or value<held_best:m=dict(m,lut=checkpoint[0]);best=checkpoint[1];stop=True
            else:held_best=value;kept+=pending;pending=0;checkpoint=(list(m['lut']),best)
        if not gained:break
    O.compile_model(m)
    m['polish']=dict(method='greedy direct LUT entries; held-out checkpoint acceptance',seeds=seeds,
                     evaluations=evaluations,kept_moves=kept,passes=passes,margin=margin,check=check,
                     start=start,final=dict(train=best,held=held_best),base_id=entry['id'])
    return dict(id=F.digest(m),base_id=entry['id'],model=m,params=m['params'],status='polished',
                evaluations=evaluations,accepted=kept,start=start,final=m['polish']['final'])


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--refined',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--top',type=int,default=7);ap.add_argument('--cap',type=int,default=12000)
    ap.add_argument('--minimum',type=float,default=200);ap.add_argument('--workers',type=int,default=7)
    a=ap.parse_args()
    if a.output.exists():ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True);protocol=json.loads((a.refined/'protocol.json').read_text())
    base=protocol['config_seed']-101;seeds=[base+711+10*i for i in range(6)]
    finalists=json.loads((a.refined/'frozen.json').read_text())['finalists'][:a.top]
    import engine as S
    S.save(a.output/'protocol.json',dict(protocol,polish_seeds=seeds,polish_cap=a.cap,polish_minimum=a.minimum,
        polish_source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}))
    start=time.monotonic()
    with mp.get_context('spawn').Pool(min(a.workers,len(finalists))) as pool:
        results=pool.map(polish,[(f,seeds,a.cap,a.minimum) for f in finalists])
    # Bases stay eligible beside their polished descendants.
    chosen=[dict(id=f['id'],model=f.get('model'),params=f['params'],metrics=f.get('metrics')) for f in finalists]
    chosen+=[dict(id=r['id'],model=r['model'],params=r['params'],polish=dict(start=r['start'],final=r['final']))
             for r in results if r.get('status')=='polished' and r['accepted'] and r['id']!=r['base_id']]
    S.save(a.output/'frozen.json',dict(finalists=chosen,no_reselection=True))
    S.save(a.output/'summary.json',dict(polished=[{k:v for k,v in r.items() if k not in ('model','params')} for r in results],
                                        finalists=len(chosen),elapsed_s=time.monotonic()-start))
    print(json.dumps(json.loads((a.output/'summary.json').read_text())),flush=True)


if __name__=='__main__':main()
