#!/usr/bin/env python3
"""PAIR range search for C5VRX by Twotoz/contributors.

Plain shared-word trackers address the LUT with only the first raw IQ40
sample of each 50ns span. PAIR decoders also address sign bits of the second
sample and use learned MMSE observation phasors (pair_decoder.py). This search
evolves complete compiled LUT trackers on both families under one budget:
parallel islands, self-adapting Gaussian steps in normalized parameter space,
crossover, topology moves and stagnation restarts. Every evaluation is a
deduplicated compiled LUT/schedule scored by the safe_range objective against
matched RANGE32. A second independent screen re-ranks leaders before freezing.
Independent confirmation remains validate_range.py; nothing is promoted here.
"""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import time
import numpy as np

CONTINUOUS={'kp':(.1,1.6,'lin'),'ki':(.005,1.8,'log'),'low_hz':(-5e6,-2e6,'lin'),
            'high_hz':(3.5e6,8e6,'lin'),'centre_hz':(-.5e6,2.2e6,'lin'),'rotation':(0,1,'lin'),
            'radius_scale':(.25,1.,'lin'),'confidence_floor':(0,.95,'lin'),'limit':(.15,np.pi,'lin'),
            'adaptation':(0,2,'lin'),'output_gain':(.1,1.3,'lin'),'advance_mix':(0,1,'lin')}
CATEGORICAL={'error_function':('linear','clip','sine','tanh'),'adaptive':(False,True),
             'confidence_output':(False,True),'centroid_observations':(False,True),
             'reconstruction':('innovation','advance','quantized_advance','mixed_advance')}
DECODERS=[('4411',mix,seed) for mix in ('uniform','weak','strong') for seed in (0,1)]
PLAIN=-1
# Amplitude mode (recorded in the protocol, default off): first-order trackers
# shrink video and sync depth under noise; this mode also searches
# phase x frequency (second-order) allocations down to 8 phases/8 tokens,
# adds the unbiased-innovation parameter and penalizes weak-signal sync-depth
# and contrast loss in the objective.
AMP=os.environ.get('C5VRX4_AMPLITUDE','0')=='1'
if AMP:CONTINUOUS['unbias']=(0,1,'lin')
# Click hold (C5VRX4_CLICK_HOLD=1, recorded): skip implausible phase steps.
CLICK=os.environ.get('C5VRX4_CLICK_HOLD','0')=='1'
if CLICK:CONTINUOUS['click_hold']=(.3,np.pi,'lin')
# Extra C/N3..4 weak cases (recorded in the protocol; default off).
EDGE=os.environ.get('C5VRX4_EDGE_SCREEN','0')=='1'


def amplitude_objective(r):
    """Weak-signal sync depth and contrast in the objective (amplitude mode)."""
    if r is None or not AMP:return r
    r=dict(r,base_score=r['score'])
    r['score']=r['score']-.5*r['weak_sync_error']-.2*max(0.,95.-r['weak_contrast'])
    return r


def topologies():
    result=[]
    for decoder in [PLAIN,*range(len(DECODERS))]:
        for b in ((3,4,5,6) if AMP else (4,5,6)):
            states=1<<(10-b)
            for phases in ((8,16,32,64) if AMP else (16,32,64)):
                if phases>states:continue
                for groups in (1,2,4):
                    if (1<<b)//groups<(8 if AMP else 16):continue
                    if decoder==PLAIN and groups>1 and b<5:continue
                    result.append((decoder,b,phases,groups))
    return result


TOPOLOGIES=topologies()


def value(name,u):
    lo,hi,kind=CONTINUOUS[name];u=min(1.,max(0.,u))
    return float(np.exp(np.log(lo)+u*(np.log(hi)-np.log(lo)))) if kind=='log' else float(lo+u*(hi-lo))


def params(genome,bank,base_seed):
    decoder,b,phases,groups=TOPOLOGIES[genome['topology']]
    u=genome['u'];p={k:value(k,u[i]) for i,k in enumerate(CONTINUOUS)}
    p.update({k:CATEGORICAL[k][genome['c'][i]] for i,k in enumerate(CATEGORICAL)})
    obs=(1<<b)//groups;p['rotation']*=2*np.pi/obs
    p.update(token_bits=b,phases=phases,confidence_groups=groups)
    if decoder==PLAIN:
        p['radius_scale']=1.2+p['radius_scale']*5.8  # plain geometry is in ADC cells
    else:
        layout,mix,seed=DECODERS[decoder]
        p.update(pair_layout=layout,pair_weight=0.,pair_rotation=0.,observation_vectors=bank[decoder],
                 observation_training=dict(layout=layout,mix=mix,seed=base_seed+601+seed))
    return p


def random_genome(rng,topology=None):
    return dict(topology=int(rng.integers(len(TOPOLOGIES))) if topology is None else topology,
                u=rng.random(len(CONTINUOUS)).tolist(),
                c=[int(rng.integers(len(v))) for v in CATEGORICAL.values()])


def mutate(g,rng,sigma):
    child=dict(topology=g['topology'],u=list(g['u']),c=list(g['c']))
    count=1+int(rng.geometric(.45))
    for i in rng.choice(len(child['u']),size=min(count,len(child['u'])),replace=False):
        child['u'][i]=float(np.clip(child['u'][i]+rng.normal(0,sigma),0,1))
    if rng.random()<.15:
        i=int(rng.integers(len(child['c'])));child['c'][i]=int(rng.integers(len(list(CATEGORICAL.values())[i])))
    return child


def crossover(a,b,rng):
    mask=rng.random(len(a['u']))<.5
    return dict(topology=a['topology'],u=[x if m else y for x,y,m in zip(a['u'],b['u'],mask)],
                c=[x if rng.random()<.5 else y for x,y in zip(a['c'],b['c'])])


def neighbour_topology(t,rng):
    decoder,b,phases,groups=TOPOLOGIES[t]
    options=[i for i,(d,bb,pp,gg) in enumerate(TOPOLOGIES) if i!=t and
             sum((d!=decoder,bb!=b,pp!=phases,gg!=groups))==1]
    return int(rng.choice(options)) if options else t


STATE={}


def screen(seed):
    """Short safe_range screen plus strong amplitude/offset envelope guards.

    Without the envelope, run A's PAIR leaders lost sync at a 0.5MHz carrier
    offset in full-field selection; offsets are jittered around the frozen
    validation envelope instead of reusing its exact values.
    """
    import search_range as R
    import waveforms as V
    import engine as S
    import lane_profile as L
    cases=R.cases(seed,L.LANE);rng=np.random.default_rng(seed+77)
    baseline=S.F.load_reference('OVP56')
    for i,(rms,cfo) in enumerate(((1.5,1e6),(5,1e6),(3,.5e6),(3,1.5e6),(2,.75e6),(4,1.25e6))):
        rms=L.scale(rms)*float(rng.uniform(.9,1.1));cfo+=float(rng.uniform(-.08e6,.08e6))
        c=V.make_case(('PAL','NTSC')[i%2],seed+20+i,30,rms,short=True,cfo_hz=cfo,
                      stimulus_seed=seed+100020+i,lane_model=L.LANE)
        for key in ('raw','clean','truth','region'):c[key]=c[key][65536:81920]
        c['calibration']=V.M.clean_calibration(S.decode(c['clean'],baseline),c['truth'],3000)
        cases.append(c)
    if EDGE:
        # Range is decided in the FM-threshold cliff: weight C/N3..4 more.
        for i,(standard,cnr) in enumerate((('PAL',3),('NTSC',3),('PAL',4),('NTSC',4))):
            c=V.make_case(standard,seed+40+i,cnr,L.scale(3),short=True,stimulus_seed=seed+100040+i,lane_model=L.LANE)
            for key in ('raw','clean','truth','region'):c[key]=c[key][65536:81920]
            c['calibration']=V.M.clean_calibration(S.decode(c['clean'],baseline),c['truth'],3000)
            cases.append(c)
    return cases


def setup(seed_base,bank):
    import search_range as R
    import engine as S
    import overlay_fsm as O
    import refine_overlay as F
    os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
    tone,_=S.tone_data();ref=S.F.load_reference('OVP56')
    means,_=S.tone_stats(S.codes(tone,ref),S.F.LEVELS)
    STATE.update(R=R,S=S,O=O,F=F,tone=tone,means=means,bank=bank,seed_base=seed_base,
                 cases=screen(seed_base+201))


def evaluate(genome):
    R,S,O,F=STATE['R'],STATE['S'],STATE['O'],STATE['F']
    p=params(genome,STATE['bank'],STATE['seed_base'])
    try:m=O.synthesize(p)
    except ValueError:return None,None,'invalid'
    key=F.digest(m)
    values,_=S.tone_stats(O.model_codes(STATE['tone'],m),S.F.LEVELS)
    if not (np.max(abs(values-STATE['means']))<=5 and np.corrcoef(values,STATE['means'])[0,1]>.98):
        return key,None,'tone_rejected'
    r=amplitude_objective(R.score(m,STATE['cases'],'safe_range'))
    return key,r,'eligible' if r else 'strong_rejected'


def worker(args):
    index,budget,seed_base,bank,output=args
    setup(seed_base,bank);rng=np.random.default_rng(seed_base+7001+index)
    islands=[dict(topology=int(rng.integers(len(TOPOLOGIES))),elites=[],sigma=.15,best=-1e9,since=0)
             for _ in range(6)]
    seen=set();counts=dict(proposals=0,unique=0,tone_rejected=0,strong_rejected=0,eligible=0,invalid=0,restarts=0)
    leaders={};start=time.monotonic();last=start
    while counts['unique']<budget:
        island=islands[int(rng.integers(len(islands)))];counts['proposals']+=1
        if counts['proposals']>30*budget:break
        elites=island['elites'];roll=rng.random()
        if not elites or roll<.08:genome=random_genome(rng,island['topology'])
        elif roll<.18 and len(elites)>1:
            a,b=rng.choice(len(elites),2,replace=False);genome=crossover(elites[a][1],elites[b][1],rng)
            genome=mutate(genome,rng,island['sigma']*.5)
        else:
            parent=elites[min(int(rng.integers(len(elites))),int(rng.integers(len(elites))))][1]
            genome=mutate(parent,rng,island['sigma'])
            if rng.random()<.03:genome['topology']=neighbour_topology(genome['topology'],rng)
        key,r,status=evaluate(genome)
        if key is None:counts['invalid']+=1;continue
        if key in seen:continue
        seen.add(key);counts['unique']+=1;counts[status]+=1
        improved=False
        if r:
            item=(r['score'],genome,r)
            elites.append(item);elites.sort(key=lambda x:-x[0]);del elites[12:]
            improved=r['score']>island['best']
            if improved:island['best']=r['score'];island['since']=0
            pool=leaders.setdefault(genome['topology'],[])
            if key not in [x[0] for x in pool]:
                pool.append((key,r['score'],genome,r));pool.sort(key=lambda x:-x[1]);del pool[6:]
        island['sigma']=float(np.clip(island['sigma']*(1.25 if improved else .985),.01,.45))
        island['since']+=1
        if island['since']>2500:
            counts['restarts']+=1;best=max((x for v in leaders.values() for x in v),key=lambda x:x[1],default=None)
            topo=int(rng.integers(len(TOPOLOGIES))) if best is None or rng.random()<.5 else best[2]['topology']
            island.update(topology=topo,elites=[],sigma=.2,best=-1e9,since=0)
            if best is not None and topo==best[2]['topology']:island['elites']=[(best[1],best[2],best[3])]
        if time.monotonic()-last>60:
            last=time.monotonic();checkpoint(output,index,counts,leaders,seen,last-start,False)
    checkpoint(output,index,counts,leaders,seen,time.monotonic()-start,True)


def checkpoint(output,index,counts,leaders,seen,seconds,done):
    # Leaders and hashes survive interruption; finalization reads only files.
    top=max((x[1] for v in leaders.values() for x in v),default=None)
    np.save(output/f'hashes-{index}.npy',np.array(sorted(int(k[:16],16) for k in seen),np.uint64))
    data=dict(index=index,counts=counts,seconds=round(seconds),best=top,done=done,
              leaders=[(k,s,g,r) for v in leaders.values() for k,s,g,r in v])
    tmp=output/f'leaders-{index}.tmp';tmp.write_text(json.dumps(data),encoding='utf-8')
    tmp.replace(output/f'leaders-{index}.json')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--seed-base',type=int,required=True)
    ap.add_argument('--evaluations',type=int,default=1000000);ap.add_argument('--workers',type=int,default=7)
    ap.add_argument('--finalize-interrupted',action='store_true',help='finalize existing checkpoints only')
    a=ap.parse_args()
    if a.finalize_interrupted:
        import pair_decoder as P
        bank=[P.learn(layout,a.seed_base+601+seed,mix)[0] for layout,mix,seed in DECODERS]
        finalize(a.output,a.workers,bank,a.seed_base,True);return
    if a.output.exists():ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    import engine as S
    import pair_decoder as P
    import search_range as R
    import overlay_fsm as O
    import refine_overlay as F
    bank=[];training=[]
    for layout,mix,seed in DECODERS:
        vectors,info=P.learn(layout,a.seed_base+601+seed,mix);bank.append(vectors);training.append(info)
    S.save(a.output/'protocol.json',dict(profile='safe_range_amp' if AMP else 'safe_range',amplitude_mode=AMP,evaluations=a.evaluations,workers=a.workers,
        config_seed=a.seed_base+101,screen_seed=a.seed_base+201,second_screen_seed=a.seed_base+251,
        selection_seed=a.seed_base+301,final_seeds=[a.seed_base+401,a.seed_base+402],stress_seed=a.seed_base+501,
        decoder_training=training,topologies=TOPOLOGIES,lane_profile=__import__('lane_profile').record(),edge_screen=EDGE,click_hold=CLICK,
        objective='safe_range: strong waveform/detail/burst guards against matched RANGE32, including six jittered amplitude/offset envelope cases; weak luma, misses, width, jitter',
        families='PAIR4411 learned observations and plain first-sample trackers under one budget',
        policy='island evolution; per-worker unique compiled LUT/schedules; union reported; second screen re-ranks; no promotion',
        source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}))
    share=[a.evaluations//a.workers+(i<a.evaluations%a.workers) for i in range(a.workers)]
    with mp.get_context('spawn').Pool(a.workers) as pool:
        pool.map(worker,[(i,share[i],a.seed_base,bank,a.output) for i in range(a.workers)])
    finalize(a.output,a.workers,bank,a.seed_base)


def finalize(output,workers,bank,seed_base,interrupted=False):
    import engine as S
    import search_range as R
    import overlay_fsm as O
    class A:pass
    a=A();a.output=output;a.workers=workers;a.seed_base=seed_base;start=time.monotonic()
    results=[json.loads((output/f'leaders-{i}.json').read_text()) for i in range(workers)]
    hashes=np.unique(np.concatenate([np.load(a.output/f'hashes-{i}.npy') for i in range(a.workers)]))
    totals={k:sum(r['counts'][k] for r in results) for k in results[0]['counts']}
    # Re-rank on a second independent screen: one leader list per topology.
    second=screen(a.seed_base+251);rows=[];done=set()
    candidates=sorted((x for r in results for x in r['leaders']),key=lambda x:-x[1])
    for key,score,genome,metrics in candidates:
        if key in done:continue
        done.add(key);p=params(genome,bank,a.seed_base);m=O.synthesize(p)
        again=amplitude_objective(R.score(m,second,'safe_range'))
        if again:rows.append(dict(id=key,params=p,metrics=metrics,second=again,
                                  topology=TOPOLOGIES[genome['topology']],
                                  combined=float((score+again['score'])/2)))
    rows.sort(key=lambda r:-r['combined']);chosen=[];families=set()
    for r in rows:
        fam=tuple(r['topology'])
        if len(chosen)<8 or fam not in families:chosen.append(r);families.add(fam)
        if len(chosen)>=20:break
    S.save(a.output/'leaders.json',[{k:v for k,v in r.items() if k!='params'}|dict(params={k:v for k,v in r['params'].items() if k!='observation_vectors'}) for r in rows])
    S.save(a.output/'frozen.json',dict(finalists=chosen,no_reselection=True))
    S.save(a.output/'summary.json',dict(totals,per_worker_unique=int(totals['unique']),union_unique=int(len(hashes)),
        interrupted=interrupted,worker_seconds=[r['seconds'] for r in results],
        second_screen_eligible=len(rows),finalists=len(chosen),elapsed_s=time.monotonic()-start,
        best_pair=next((r['combined'] for r in rows if r['topology'][0]!=PLAIN),None),
        best_plain=next((r['combined'] for r in rows if r['topology'][0]==PLAIN),None)))
    print(json.dumps(json.loads((a.output/'summary.json').read_text())),flush=True)


if __name__=='__main__':main()
