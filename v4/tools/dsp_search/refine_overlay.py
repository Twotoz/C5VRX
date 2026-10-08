#!/usr/bin/env python3
"""Exhaust a declared small grid, preserve families, then fit actual DAC tables.

Complements the large evolutionary sweeps. New encoders/state schedules receive
the same filtered-video fitting budget; final full-field gates remain unchanged.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3
import time
import numpy as np
import engine as S
import overlay_fsm as O
import reconstruction as F
import search_overlay as R
from video_metrics import waveform,detail,relative_quality


def digest(m):
    p=m['params']
    schedule=b'counter_phase' if p.get('counter_phase') else b''
    return hashlib.sha256(np.array(m['lut'],np.uint16).tobytes()+bytes([p['token_bits'],p.get('context_bits',0)])+schedule).hexdigest()


def grid():
    """Every feasible layout in this Cartesian grid, with no guided pruning."""
    for b in range(2,7):
        for exponent in range(2,11-b):
            for groups,context,kp,limit,mix in itertools.product((1,2,4),(0,2),(.65,.85,1),(.8,1.4,np.pi),(0,.35)):
                if (1<<b)//groups<4:continue
                for weight in ((.15,.35) if context else (0,)):
                    yield dict(token_bits=b,phases=1<<exponent,confidence_groups=groups,
                        kp=kp,ki=.15,low_hz=-3.5e6,high_hz=5.5e6,centre_hz=1e6,
                        rotation=np.pi/(1<<b),radius_scale=3,confidence_floor=.2,
                        error_function='clip',limit=limit,adaptive=True,adaptation=1,
                        output_gain=.8,confidence_output=True,centroid_observations=True,
                        reconstruction='mixed_advance',advance_mix=mix,
                        context_bits=context,context_weight=weight,context_radius=3)


def prepare(cases):
    ref=S.F.load_reference('OVP56')
    for c in cases:
        y=S.decode(c['raw'],ref);c['reference']=waveform(y,c);c['reference_detail']=detail(y,c)
    return cases


def assess(m,cases):
    failures=[];weak=[];weak_values=[];missing=0;strong=[];detail_loss=0
    for c in cases:
        y=O.decode(c['raw'],m);r=waveform(y,c);ref=c['reference']
        if c['cnr']>=20:
            failures+=(relative_quality if c['stress'] or c['rms']<2 else R.quality)(r,ref)
            loss=max(0,c['reference_detail']-.03-detail(y,c));detail_loss+=loss
            if loss:failures.append('fine_detail')
            strong.append(r['sinad'])
        else:
            weak.append((c['stress'],r['sinad']-ref['sinad']))
            weak_values.append(r['sinad'])
            missing+=r['h_missing']+r['v_missing']
    return dict(failures=failures,weak_gain=float(np.mean([d for _,d in weak])),weak_sinad=float(np.mean(weak_values)),
                stress_gain=float(np.mean([d for s,d in weak if s])),missing=missing,
                strong=float(np.mean(strong)),detail_loss=detail_loss)


def rank(r):
    return (len(r['failures']),r['detail_loss'],max(0,-r['stress_gain']-.05),r['missing'],-r['weak_gain'])


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--initial',type=Path,help='previous frozen models; fresh final seeds still required')
    ap.add_argument('--seed-base',type=int,default=17000)
    ap.add_argument('--families',type=int,default=24)
    a=ap.parse_args()
    if a.output.exists():ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True);start=time.monotonic()
    S.save(a.output/'protocol.json',dict(train_seed=a.seed_base+101,screen_seed=a.seed_base+201,
        selection_seed=a.seed_base+301,final_seeds=[a.seed_base+401,a.seed_base+402],
        stress_seed=a.seed_base+501,stimulus_variation=True,
        strategy='exhaustive declared grid + equal-budget filtered DAC refinement; no final reselection',
        strong_weights=[2,8,32],edge_weights=[1,8],blends=[.25,.5,1],lsmr_iterations=60,
        source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}))
    train=F.dataset(a.seed_base+101);screen=prepare(F.dataset(a.seed_base+201))
    # All models are scored with identical source, masks and optimization effort.
    tone,_=S.tone_data();mean,_=S.tone_stats(S.codes(tone,S.F.load_reference('OVP56')),S.F.LEVELS)
    seen=set();families={};count=tone_pass=0
    proposals=list(grid())
    if a.initial:proposals += [r['params'] for r in json.loads(a.initial.read_text())['finalists']]
    db=sqlite3.connect(a.output/'leaderboard.sqlite')
    db.execute('CREATE TABLE candidates (id TEXT PRIMARY KEY,params TEXT,status TEXT,tone_error REAL,score REAL,weak_sinad REAL,weak_missing INTEGER,strong_sinad REAL)')
    for p in proposals:
        m=O.synthesize(p);key=digest(m)
        if key in seen:continue
        seen.add(key);count+=1;err=float('inf')
        means,_=S.tone_stats(O.codes(tone,np.array(m['lut'],np.uint16),p['token_bits'],p.get('context_bits',0)),S.F.LEVELS)
        err=float(np.max(abs(means-mean)))
        if err>4 or np.corrcoef(means,mean)[0,1]<.98:
            db.execute('INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?)',(key,json.dumps(p),'grid_tone_rejected',err,None,None,None,None))
            continue
        tone_pass+=1;r=assess(m,screen)
        db.execute('INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?)',
            (key,json.dumps(p),'grid_screened',err,r['weak_gain']-len(r['failures']),r['weak_sinad'],r['missing'],r['strong']))
        family=(p['token_bits'],p['phases'],p['confidence_groups'],p.get('context_bits',0))
        item=dict(id=key,model=m,metrics=r)
        if family not in families or rank(r)<rank(families[family]['metrics']):families[family]=item
        if count%200==0:print('GRID',count,'tone',tone_pass,'families',len(families),'seconds',round(time.monotonic()-start),flush=True)
    selected=sorted(families.values(),key=lambda r:rank(r['metrics']))[:a.families]
    S.save(a.output/'grid_frontier.json',dict(models=selected))
    fitted=[];fits=0
    for entry in selected:
        m=entry['model']
        for sw,ew in itertools.product((2,8,32),(1,8)):
            values,info=F.fit(m,train,strong_weight=sw,edge_weight=ew);fits+=1
            for blend in (.25,.5,1):
                fm=F.reconstruct(m,values,blend);key=digest(fm)
                if key in seen:continue
                seen.add(key);fm['fit']=dict(info,blend=blend,base_id=entry['id'],seed=a.seed_base+101)
                r=assess(fm,screen);item=dict(id=key,model=fm,params=fm['params'],metrics=r)
                fitted.append(item)
                db.execute('INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?)',
                    (key,json.dumps(dict(fm['params'],fit=fm['fit'])),'refined',None,
                     r['weak_gain']-len(r['failures']),r['weak_sinad'],r['missing'],r['strong']))
        db.commit();print('FIT',fits,'unique reconstructions',len(fitted),'best',min((rank(r['metrics']) for r in fitted),default=None),flush=True)
    # Keep topology diversity; screening imperfections may be rehabilitated by
    # full-field selection, which alone applies the final hard quality gates.
    fitted.sort(key=lambda r:rank(r['metrics']));chosen=fitted[:8];topologies=set()
    for entry in chosen:
        p=entry['params'];topologies.add((p['token_bits'],p['phases'],p['confidence_groups'],p.get('context_bits',0)))
    for entry in fitted[8:]:
        p=entry['params'];top=(p['token_bits'],p['phases'],p['confidence_groups'],p.get('context_bits',0))
        if top not in topologies:chosen.append(entry);topologies.add(top)
        if len(chosen)>=16:break
    S.save(a.output/'frozen.json',dict(finalists=chosen,no_reselection=True))
    summary=dict(grid_proposals=len(proposals),unique_grid_evaluations=count,tone_pass=tone_pass,
                 grid_duplicates=len(proposals)-count,fit_duplicates=len(selected)*18-len(fitted),
                 topology_families=len(families),refined_families=len(selected),lsmr_fits=fits,
                 unique_reconstructions=len(fitted),finalists=len(chosen),elapsed_s=time.monotonic()-start)
    S.save(a.output/'summary.json',summary);db.close();print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
