#!/usr/bin/env python3
"""Robust multi-screen selection of PAIR/plain search leaders (C5VRX).

Strong-picture and colour-burst guards on one short screen are noisy: a
candidate can pass by luck. Every checkpointed leader is re-scored on six
fresh screens; ranking is guard-pass count first, then mean weak objective.
Guards and objective are unchanged (safe_range against matched RANGE32).
The selected set still requires validate_range.py independent confirmation.
"""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import time
import numpy as np

STATE={}


def setup(seeds):
    os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
    import search_range as R
    import search_pair_range as Q
    STATE.update(R=R,screens=[Q.screen(s) for s in seeds])


def assess(item):
    import overlay_fsm as O
    R=STATE['R'];key,params=item;m=O.synthesize(params);passes=0;scores=[];weak=[]
    for cases in STATE['screens']:
        r=R.score(m,cases,'safe_range')
        if r:passes+=1;scores.append(r['score']);weak.append((r['weak_luma_sinad'],r['weak_missing'],r['strong_sinad'],r['detail']))
    mean=np.mean(weak,axis=0).tolist() if weak else None
    return dict(id=key,passes=passes,score=float(np.mean(scores)) if scores else None,
                weak_luma=mean and mean[0],weak_missing=mean and mean[1],strong_sinad=mean and mean[2],detail=mean and mean[3])


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--search',type=Path,required=True,nargs='+');ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--workers',type=int,default=7);ap.add_argument('--keep',type=int,default=16)
    a=ap.parse_args()
    if a.output.exists():ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    import engine as S
    import pair_decoder as P
    import search_pair_range as Q
    protocol=json.loads((a.search[0]/'protocol.json').read_text());base=protocol['config_seed']-101
    seeds=[base+811+10*i for i in range(6)];items={};origin={}
    for directory in a.search:
        p=json.loads((directory/'protocol.json').read_text());sb=p['config_seed']-101
        bank=[P.learn(layout,sb+601+seed,mix)[0] for layout,mix,seed in Q.DECODERS]
        for f in sorted(directory.glob('leaders-*.json')):
            for key,score,genome,metrics in json.loads(f.read_text())['leaders']:
                if key not in items:items[key]=Q.params(genome,bank,sb);origin[key]=(directory.name,Q.TOPOLOGIES[genome['topology']])
    S.save(a.output/'protocol.json',dict(protocol,robust_selection_seeds=seeds,robust_sources=[str(d) for d in a.search],
        candidates=len(items),rule='rank by guard-pass screens (of six, each with amplitude/offset envelope), then mean safe_range score; no guard changes',
        robust_source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}))
    start=time.monotonic()
    with mp.get_context('spawn').Pool(a.workers,initializer=setup,initargs=(seeds,)) as pool:
        rows=pool.map(assess,list(items.items()),chunksize=8)
    for r in rows:r['source'],r['topology']=origin[r['id']]
    rows.sort(key=lambda r:(-r['passes'],-(r['score'] if r['score'] is not None else -1e9)))
    chosen=[];families=set()
    for r in rows:
        fam=tuple(r['topology'])
        if len(chosen)<a.keep//2 or fam not in families:chosen.append(r);families.add(fam)
        if len(chosen)>=a.keep:break
    S.save(a.output/'ranking.json',rows)
    S.save(a.output/'frozen.json',dict(finalists=[dict(id=r['id'],params=items[r['id']],metrics=r) for r in chosen],no_reselection=True))
    summary=dict(candidates=len(rows),all_six=sum(r['passes']==6 for r in rows),
                 all_six_pair=sum(r['passes']==6 and r['topology'][0]!=Q.PLAIN for r in rows),
                 all_six_plain=sum(r['passes']==6 and r['topology'][0]==Q.PLAIN for r in rows),
                 finalists=len(chosen),elapsed_s=time.monotonic()-start)
    S.save(a.output/'summary.json',summary);print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
