#!/usr/bin/env python3
"""Millions-scale C5VRX range-priority search with explicit picture trade-offs."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import numpy as np
from numba import njit
import engine as S
import overlay_fsm as O
import search_overlay as R
import range_objective as J
import refine_overlay as F
import waveforms as V
import lane_profile as L

PINNED=json.loads((Path(__file__).parents[1]/'range32_model.json').read_text())


def propose(rng):
    p=O.propose(rng,extended=True)
    p.update(context_bits=2 if rng.random()<.2 else 0,
             context_weight=float(rng.uniform(.03,.5)),context_radius=float(rng.uniform(1,6)))
    if rng.random()<.05:
        p.update(token_bits=5,phases=32,counter_phase=True,address_bias=float(rng.uniform(0,1023)))
    return p


def mutate(p,rng):
    q=R.mutate(p,rng)
    if rng.random()<.2:
        for key in ('context_weight','context_radius'):
            if key in q:q[key]=propose(rng)[key]
    if q.get('counter_phase') and rng.random()<.2:q['address_bias']=float(rng.uniform(0,1023))
    return q


def cases(seed,lane_model=None):
    baseline=S.F.load_reference('OVP56');result=[]
    for standard,cnr,stress in [('PAL',30,False),('NTSC',30,False),
                               ('PAL',2,False),('PAL',6,False),('NTSC',6,False),
                               ('NTSC',10,False),('PAL',6,True)]:
        c=V.make_case(standard,seed,cnr,L.scale(3),short=True,stress=stress,stimulus_seed=seed+100000,lane_model=lane_model)
        for key in ('raw','clean','truth','region'):c[key]=c[key][65536:81920]
        c['calibration']=V.M.clean_calibration(S.decode(c['clean'],baseline),c['truth'],3000)
        result.append(c)
    return result


def score(m,prepared,profile):
    rows=[]
    for c in prepared:
        include_burst=c['cnr']>=20 and J.PROFILES[profile].get('burst_guard',False)
        r=J.measure(O.decode(c['raw'],m),c,include_burst)
        if c['cnr']>=20:
            reference=None
            if J.PROFILES[profile].get('reference_guard'):
                if '_range_reference' not in c:c['_range_reference']=J.measure(O.decode(c['raw'],PINNED),c,include_burst)
                reference=c['_range_reference']
            if J.strong_failures(r,profile,reference):return None
        rows.append((c,r))
    weak=[r for c,r in rows if c['cnr']<20];strong=[r for c,r in rows if c['cnr']>=20]
    missing=sum(r['h_missing']+r['v_missing'] for r in weak)
    luma=float(np.mean([r['luma_sinad'] for r in weak]));sinad=float(np.mean([r['sinad'] for r in weak]))
    guard=J.PROFILES[profile]
    width=float(np.mean([min(r['h_width_rmse_us'],2.025) for r in weak]))
    jitter=float(np.mean([r['h_jitter_us']+r['v_jitter_us'] for r in weak]))
    return dict(score=luma-1.5*missing-guard.get('weak_width_weight',0)*width-guard.get('weak_jitter_weight',0)*jitter,
                weak_sinad=sinad,weak_missing=missing,
                weak_luma_sinad=luma,strong_sinad=float(np.mean([r['sinad'] for r in strong])),
                detail=float(np.mean([r['detail_corr'] for r in strong])))


@njit(cache=True)
def quick_luma(y,truth,lag,gain,offset):
    """Cheap shared-calibration information proxy; never final video acceptance."""
    lo=max(512,-lag);hi=min(len(y),len(truth)-lag)
    a=t=sa=st=saa=stt=sat=error=0.;count=0
    alpha=1-np.exp(-2*np.pi*700000/40000000)
    for k in range(lo,hi):
        a+=alpha*(gain*y[k+lag]+offset-a);t+=alpha*(truth[k]-t)
        if k<lo+200:continue
        count+=1;sa+=a;st+=t;saa+=a*a;stt+=t*t;sat+=a*t;error+=(a-t)**2
    variance=max(stt/count-(st/count)**2,1e-20)
    sinad=10*np.log10(variance/max(error/count,1e-20))
    contrast=100*(sat/count-sa*st/(count*count))/variance
    return sinad,contrast


def quick(m,prepared,profile):
    values=[]
    # Two clean strong cases, two weak formats and one echo/fade case.
    for index in (0,1,3,4,6):
        c=prepared[index];raw=c['raw'][:8192]
        y=O.decode(raw,m);lag,gain,offset=c['calibration']
        value,contrast=quick_luma(y,c['truth'][:8192],lag,gain,offset)
        if index<2 and (value<7 or contrast<50 or contrast>150):return None
        if index>=2:values.append(value)
    return float(np.mean(values))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--profile',choices=J.PROFILES,required=True)
    ap.add_argument('--seed-base',type=int,required=True);ap.add_argument('--evaluations',type=int,default=1000000)
    a=ap.parse_args()
    if a.output.exists():ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True);rng=np.random.default_rng(a.seed_base+101)
    S.save(a.output/'protocol.json',dict(profile=a.profile,guards=J.PROFILES[a.profile],
        common_usable='SINAD>=5; contrast50..150; H misses<=2%, gap<=3; V misses0/trains2; jitter<=.5us,widthRMSE<=.5us',
        evaluations=a.evaluations,config_seed=a.seed_base+101,screen_seed=a.seed_base+201,
        selection_seed=a.seed_base+301,final_seeds=[a.seed_base+401,a.seed_base+402],stress_seed=a.seed_base+501,
        objective=('weak range with reference strong waveform/detail guards; fine ADC from search through confirmation'
                   if J.PROFILES[a.profile].get('reference_guard') else
                   'range priority; intentional strong detail/colour trade-off; not measured RF range'),
        staging='tone -> approximate low-band information on five8192-byte cases -> detailed short-video screen for topology top8 and1/256 audits; independent full fields later',
        source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}))
    prepared=cases(a.seed_base+201,'fine' if J.PROFILES[a.profile].get('fine_lane') else None)
    tone,_=S.tone_data();ref=S.F.load_reference('OVP56')
    means,_=S.tone_stats(S.codes(tone,ref),S.F.LEVELS)
    pinned=json.loads((Path(__file__).parents[1]/'range32_model.json').read_text())
    # Analytical mutations do not inherit the learned output; the pinned
    # control is preserved separately and finalists receive equal DAC fitting.
    parents=[dict(params=pinned['params'],score=0)];leaders={};seen=set();quick_leaders={}
    db=sqlite3.connect(a.output/'leaderboard.sqlite')
    db.execute('CREATE TABLE candidates(id TEXT PRIMARY KEY,params TEXT,status TEXT,tone_error REAL,score REAL,weak_sinad REAL,weak_missing INTEGER,strong_sinad REAL)')
    attempts=count=tone_pass=eligible=quick_pass=full_screened=0;start=time.monotonic()
    while count<a.evaluations:
        attempts+=1
        if attempts>15*a.evaluations:raise RuntimeError('unique behaviour budget exhausted')
        p=mutate(parents[int(rng.integers(len(parents)))]['params'],rng) if rng.random()<.65 else propose(rng)
        m=O.synthesize(p);key=F.digest(m)
        if key in seen:continue
        seen.add(key);count+=1;lut=np.array(m['lut'],np.uint16)
        values,_=S.tone_stats(O.codes(tone,lut,p['token_bits'],p.get('context_bits',0),p.get('counter_phase',False)),S.F.LEVELS)
        err=float(np.max(abs(values-means)));r=None;status='tone_rejected'
        if err<=5 and np.corrcoef(values,means)[0,1]>.98:
            tone_pass+=1;fast=quick(m,prepared,a.profile);status='quick_rejected'
            family=(p['token_bits'],p['phases'],p['confidence_groups'],p.get('context_bits',0),p.get('counter_phase',False))
            if fast is not None:
                quick_pass+=1;status='quick_screened';pool=quick_leaders.setdefault(family,[])
                # Retain eight proxy proposals per topology and randomly audit
                # discarded proposals. Final quality never uses this shortcut.
                inspect=len(pool)<8 or fast>min(pool) or rng.random()<1/256
                pool.append(fast);pool.sort(reverse=True);del pool[8:]
                if inspect:
                    full_screened+=1;r=score(m,prepared,a.profile);status='strong_rejected'
            if r:
                eligible+=1;status='proxy_eligible';item=dict(id=key,params=p,**r)
                old=leaders.get(family)
                if old is None or r['score']>old['score']:leaders[family]=item
                parents.append(item);parents.sort(key=lambda x:-x['score']);del parents[64:]
        db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?)',(key,json.dumps(p),status,err,
                   r['score'] if r else None,r['weak_sinad'] if r else None,r['weak_missing'] if r else None,r['strong_sinad'] if r else None))
        if count%5000==0:
            db.commit();print(a.profile,count,'proposals',attempts,'tone',tone_pass,'full',full_screened,'eligible',eligible,'seconds',round(time.monotonic()-start),flush=True)
    db.commit();db.close()
    chosen=sorted(leaders.values(),key=lambda r:-r['score'])[:24]
    S.save(a.output/'frozen.json',dict(finalists=chosen,no_reselection=True))
    S.save(a.output/'summary.json',dict(unique_evaluations=count,proposals=attempts,duplicates=attempts-count,
           tone_pass=tone_pass,quick_pass=quick_pass,full_screened=full_screened,
           proxy_eligible=eligible,families=len(leaders),finalists=len(chosen),elapsed_s=time.monotonic()-start))


if __name__=='__main__':main()
