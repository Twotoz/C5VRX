#!/usr/bin/env python3
"""Exhaust and equally refine an actual ADDCTIA residual-phase schedule."""
import argparse
import itertools
import json
from pathlib import Path
import numpy as np
import reconstruction as F
import refine_overlay as R
import overlay_fsm as O
import engine as S


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--initial',type=Path,required=True);a=ap.parse_args()
    if a.output.exists():ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    S.save(a.output/'protocol.json',dict(train_seed=21101,screen_seed=21201,selection_seed=21301,
        final_seeds=[21401,21402],stress_seed=21501,stimulus_variation=True,selection_envelope=True,
        scope='counter residual grid compared with frozen learned-state families on new independent signals'))
    train=F.dataset(21101);screen=R.prepare(F.dataset(21201));families={};rows=[];seen=set()
    tone,_=S.tone_data();ref,_=S.tone_stats(S.codes(tone,S.F.load_reference('OVP56')),S.F.LEVELS)
    for context,kp,limit,adapt,mix,centre in itertools.product((0,2),(.5,.75,1,1.25),(.7,1.2,np.pi),(0,1.5),(0,.35),(.5e6,1e6,1.5e6)):
        p=dict(token_bits=5,phases=32,confidence_groups=1,kp=kp,ki=.1,
               low_hz=-3.5e6,high_hz=5.5e6,centre_hz=centre,rotation=np.pi/32,
               radius_scale=3,confidence_floor=.2,error_function='clip',limit=limit,
               adaptive=True,adaptation=adapt,output_gain=.8,confidence_output=False,
               centroid_observations=True,reconstruction='mixed_advance',advance_mix=mix,
               context_bits=context,context_weight=.15,context_radius=3,counter_phase=True,
               address_bias=511.5 if context else 127.5)
        m=O.synthesize(p);O.compile_model(m);key=R.digest(m)
        if key in seen:continue
        seen.add(key)
        mean,_=S.tone_stats(O.codes(tone,np.array(m['lut'],np.uint16),5,context,True),S.F.LEVELS)
        err=float(np.max(abs(mean-ref)))
        if err>4 or np.corrcoef(mean,ref)[0,1]<.98:
            rows.append(dict(id=key,params=p,status='tone_rejected',tone_error=err));continue
        metrics=R.assess(m,screen);entry=dict(id=key,params=p,model=m,metrics=metrics,status='screened')
        rows.append(entry);family=(context,centre)
        bucket=families.setdefault(family,[]);bucket.append(entry);bucket.sort(key=lambda r:R.rank(r['metrics']));del bucket[2:]
    fitted=[];fits=0
    for bucket in families.values():
        for entry in bucket:
            for sw,ew in itertools.product((2,8,32),(1,8)):
                values,info=F.fit(entry['model'],train,strong_weight=sw,edge_weight=ew);fits+=1
                for blend in (.25,.5,1):
                    m=F.reconstruct(entry['model'],values,blend);key=R.digest(m)
                    if key in seen:continue
                    seen.add(key);m['fit']=dict(info,blend=blend,base_id=entry['id'],seed=21101)
                    fitted.append(dict(id=key,params=m['params'],model=m,metrics=R.assess(m,screen)))
            print('COUNTER FIT',fits,'best',min(R.rank(r['metrics']) for r in fitted),flush=True)
    fitted.sort(key=lambda r:R.rank(r['metrics']))
    eligible=[r for r in fitted if not r['metrics']['failures'] and r['metrics']['stress_gain']>=-.05]
    initial=json.loads(a.initial.read_text())['finalists']
    # A separate confirmation is warranted only if a counter passes its screen.
    chosen=initial+eligible[:8] if eligible else []
    S.save(a.output/'frozen.json',dict(finalists=chosen,no_reselection=True))
    S.save(a.output/'leaderboard.json',dict(grid=rows,fitted=fitted))
    result=dict(unique_grid_evaluations=len(rows),lsmr_fits=fits,unique_reconstructions=len(fitted),
                counter_proxy_eligible=len(eligible),finalists=len(chosen))
    S.save(a.output/'summary.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':main()
