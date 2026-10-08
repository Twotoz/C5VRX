#!/usr/bin/env python3
"""Matched-work brute/random/guided comparison in the same declared grid.

Each gets64 unique short-video evaluations and six capped60-iteration fits,
with two independent starts and identical blind full-field quality gates.
Measured runtime is reported; fixed work caps are not exactly equal CPU time.
"""
import argparse
import csv
import itertools
import json
from pathlib import Path
import time
import numpy as np
import engine as S
import reconstruction as F
import refine_overlay as R
import overlay_fsm as O
import validate_overlay as V
import validate_search as Q


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    if a.output.exists():ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True);train=F.dataset(23101);screen=R.prepare(F.dataset(23201))
    pool=list(R.grid());families={}
    for k,p in enumerate(pool):
        key=(p['token_bits'],p['phases'],p['confidence_groups'],p.get('context_bits',0))
        families.setdefault(key,[]).append(k)
    balanced=[]
    for layer in range(max(map(len,families.values()))):
        balanced += [indices[layer] for indices in families.values() if layer<len(indices)]
    metrics_cache={};model_cache={};curves=[];finalists=[];summaries=[]
    def evaluate(index):
        # Replayed cost is measured on first evaluation, independent of strategy.
        if index not in metrics_cache:
            start=time.perf_counter();m=O.synthesize(pool[index]);O.compile_model(m)
            r=R.assess(m,screen);metrics_cache[index]=(r,time.perf_counter()-start);model_cache[index]=m
        return metrics_cache[index]
    for start_seed in (23361,23362):
        starter=np.random.default_rng(start_seed).choice(len(pool),16,replace=False).tolist()
        for strategy in ('exhaustive-balanced','random','guided'):
            rng=np.random.default_rng(start_seed);visited=set();ranked=[];cost=0;wall=time.perf_counter()
            for iteration in range(64):
                if iteration<16:index=starter[iteration]
                elif strategy=='exhaustive-balanced':index=next(k for k in balanced if k not in visited)
                elif strategy=='random':
                    index=int(rng.choice([k for k in range(len(pool)) if k not in visited]))
                else:
                    parent=pool[ranked[int(rng.integers(min(8,len(ranked))))][1]]
                    neighbors=[k for k,p in enumerate(pool) if k not in visited and
                               sum(p[key]!=parent[key] for key in parent)==1]
                    index=int(rng.choice(neighbors or [k for k in range(len(pool)) if k not in visited]))
                visited.add(index);r,elapsed=evaluate(index);cost+=elapsed
                ranked.append((R.rank(r),index));ranked.sort()
                curves.append(dict(strategy=strategy,start=start_seed,evaluations=iteration+1,
                    best_failures=ranked[0][0][0],best_missing=ranked[0][0][3],
                    accumulated_evaluation_seconds=cost))
            base=model_cache[ranked[0][1]];choices=[];fit_iterations=0;fit_start=time.perf_counter()
            for sw,ew in itertools.product((2,8,32),(1,8)):
                values,info=F.fit(base,train,strong_weight=sw,edge_weight=ew)
                fit_iterations+=info['iterations']
                for blend in (.25,.5,1):
                    m=F.reconstruct(base,values,blend);m['fit']=dict(info,blend=blend,seed=23101)
                    choices.append((R.rank(R.assess(m,screen)),m))
            _,winner=min(choices,key=lambda x:x[0]);name=strategy+'-'+str(start_seed)
            winner['name']=name;finalists.append(winner)
            summaries.append(dict(strategy=strategy,start=start_seed,evaluations=64,lsmr_fits=6,
                fit_iterations=fit_iterations,evaluation_seconds=cost,fit_seconds=time.perf_counter()-fit_start,
                observed_wall_seconds=time.perf_counter()-wall,
                cost_scope='evaluation runtime replayed from common cache; fitting runtime measured independently'))
            print('STRATEGY',strategy,start_seed,'base_rank',ranked[0][0],flush=True)
    # Freeze every choice before reading any independent confirmation waveform.
    S.save(a.output/'frozen.json',dict(models=finalists,work=summaries))
    Q.write_rows(a.output/'curves.csv',curves)
    rows=V.evaluate(finalists,23401,(8,30),'strategy_final',stimulus_seed=123401)
    rows+=V.evaluate(finalists,23402,(8,30),'strategy_channel',True,stimulus_seed=123402)
    rows+=V.evaluate(finalists,23403,(30,),'strategy_offset',cfo_hz=.5e6,stimulus_seed=123403)
    rows+=V.evaluate(finalists,23404,(30,),'strategy_occupancy',rms=5,stimulus_seed=123404)
    Q.write_rows(a.output/'confirmation.csv',rows)
    result=V.aggregate(rows)
    S.save(a.output/'summary.json',dict(work=summaries,confirmation=result,
        scope='two starts,small matched-work pilot in one finite pool; no general search superiority/global optimum claim'))
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
