#!/usr/bin/env python3
"""Equal bounded fitting effort for range-first C5 architecture finalists."""
import argparse
import itertools
import hashlib
import json
from pathlib import Path
import numpy as np
import engine as S
import overlay_fsm as O
import reconstruction as F
import distill_overlay as D
import refine_overlay as H
import search_range as R


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--search',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    if a.output.exists():ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True);protocol=json.loads((a.search/'protocol.json').read_text())
    profile=protocol['profile'];seed=protocol['config_seed']-101
    S.save(a.output/'protocol.json',dict(protocol,refinement_train_seed=seed+111,
        refinement_screen_seed=seed+211,lsmr_cap=48,fits_per_base=6,blends=[.25,.5,1],
        refinement_source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},
        teacher_scope='received-IQ floating phase/frequency targets only during training; true CVBS output fit'))
    train=F.dataset(seed+111);screen=R.cases(seed+211);teacher=D.teachers(train)
    bases=[O.synthesize(r['params']) for r in json.loads((a.search/'frozen.json').read_text())['finalists']]
    pinned=json.loads((Path(__file__).parents[1]/'range32_model.json').read_text());bases.append(pinned)
    seen=set();results=[];fits=0
    for base in bases:
        key=H.digest(base)
        if key not in seen:
            seen.add(key);metric=R.score(base,screen,profile)
            if metric:results.append(dict(id=key,model=base,params=base['params'],metrics=metric))
        if base['params'].get('counter_phase'):
            variants=[(base,sw,ew) for sw,ew in itertools.product((1,4,16),(4,16))]
        else:
            variants=[(D.project(base,train,teacher,mix) if mix else base,sw,16)
                      for mix,sw in itertools.product((0,.25,.5),(1,8))]
        for state,sw,ew in variants:
            values,info=F.fit(state,train,strong_weight=sw,edge_weight=ew,iterations=48);fits+=1
            for blend in (.25,.5,1):
                m=F.reconstruct(state,values,blend);key=H.digest(m)
                if key in seen:continue
                seen.add(key);m['fit']=dict(info,blend=blend,seed=seed+111,base_id=H.digest(base))
                metric=R.score(m,screen,profile)
                if metric:results.append(dict(id=key,model=m,params=m['params'],metrics=metric))
        print('REFINE',profile,'fits',fits,'unique',len(seen),'eligible',len(results),flush=True)
    results.sort(key=lambda r:-r['metrics']['score']);chosen=results[:8];families=set()
    def family(r):
        p=r['params'];return (p['token_bits'],p['phases'],p['confidence_groups'],p.get('context_bits',0),p.get('counter_phase',False))
    families.update(map(family,chosen))
    for r in results[8:]:
        if family(r) not in families:chosen.append(r);families.add(family(r))
        if len(chosen)>=16:break
    S.save(a.output/'leaderboard.json',dict(candidates=results))
    S.save(a.output/'frozen.json',dict(finalists=chosen,no_reselection=True))
    S.save(a.output/'summary.json',dict(unique_evaluations=len(seen),eligible=len(results),lsmr_fits=fits,finalists=len(chosen)))


if __name__=='__main__':main()
