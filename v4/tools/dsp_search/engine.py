#!/usr/bin/env python3
"""Hardware-first architecture discovery for C5VRX by Twotoz/contributors.

Typed expressions evolve into legal LUT8 pair/context/state4 programs. Unknown
history/resource demands fail before signal evaluation. No firmware promotion
without independent full-waveform confirmation; no global optimum claim.
"""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import numpy as np
from numba import njit
import hardware as H
import expressions as E
import waveforms as V
import weak_signal_fit as F


@njit(cache=True)
def rollout(raw,encoder,table,n,shift,bits,tracking):
    out=np.zeros(len(raw),np.uint8)
    previous=0;state=0;context=0
    for k in range(len(raw)//2-1):
        current=np.int64(encoder[raw[2*k]])
        row=previous*4+state if tracking else previous*(1<<len(bits))+context
        value=np.int64(table[row*n+current])
        out[2*k+2]=out[2*k+3]=value&63
        state=value>>6;previous=current>>shift
        context=0
        for j in range(len(bits)):context|=((raw[2*k+1]>>bits[j])&1)<<j
    return out


def codes(raw,m):
    table=m['packed_map'] if m.get('tracking') else m['map']
    return rollout(raw,np.asarray(m['encoder'],np.uint8),np.asarray(table,np.uint8).ravel(),
                   m['current_tokens'],m['previous_shift'],np.asarray(m['middle_bits'],np.int64),bool(m.get('tracking')))


def decode(raw,m):return H.B.D.goggle(H.B.DAC_VOLTS[codes(raw,m)])


def pool():
    rng=np.random.default_rng(9101)
    result=[F.load_reference('OVP56'),F.load_reference('VLP56')]
    for layout in H.B.layouts():
        family=('phase','polar','cartesian')[len(result)%3]
        m=dict(layout,encoder=H.B.initialize(layout['current_tokens'],family,rng).tolist(),name='pool')
        result.append(m)
    state=dict(current_tokens=56,previous_tokens=28,previous_shift=3,middle_bits=[],stride=64,
               span_ns=50,tracking=True,state_count=4,history_centres=[-.75,-.05,.65,1.35],
               encoder=result[0]['encoder'],name='state4')
    result.append(state)
    for m in result:
        m.setdefault('previous_tokens',28);m.setdefault('stride',64);m.setdefault('span_ns',50)
        m.setdefault('previous_shift',1);m.setdefault('middle_bits',[])
        m['_features']=E.features(m)
    return result


def synthesize(template,expr,reconstruction):
    E.validate(expr,stateful=bool(template.get('tracking')))
    phase=E.evaluate(expr,template['_features'])
    if not np.isfinite(phase).all():raise ValueError('nonfinite expression')
    code=np.clip((phase*128/np.pi-H.B.P.OFFSET)*H.B.P.SCALE,0,63)
    if reconstruction=='physical':
        code=np.argmin(abs(code[...,None]/63*F.LEVELS[-1]-F.LEVELS),axis=-1)
    else:code=np.rint(code).astype(int)
    m={k:copy.deepcopy(v) for k,v in template.items() if not k.startswith('_')}
    m.update(name='discovered',expression=expr,reconstruction=reconstruction,map=code.tolist())
    if m.get('tracking'):
        centres=np.asarray(m['history_centres'])
        next_state=np.argmin(abs(phase[...,None]-centres),axis=-1)
        m['packed_map']=(code+64*next_state).tolist()
    resource=H.cost(m)
    payload=bytes(m['encoder'])+np.array(m.get('packed_map',m['map']),np.uint8).tobytes()
    layout=json.dumps({k:m[k] for k in ('current_tokens','previous_shift','middle_bits')}).encode()
    digest=hashlib.sha256(layout+payload).hexdigest()
    return m,digest,resource


def tone_data():
    frequencies=np.array([-2.35e6,-1e6,-.435714e6,0,1e6,2e6,3e6,4.35e6])
    segments=[]
    for radius in (3.,5.):
        for f in frequencies:
            t=np.arange(512)/40e6
            segments.append(H.B.D.raw_bytes(radius*np.exp(2j*np.pi*f*t)).astype(np.uint8))
    return np.concatenate(segments),frequencies


@njit(cache=True)
def tone_stats(code,levels):
    mean=np.zeros(len(code)//512);var=mean.copy()
    for j in range(len(mean)):
        for k in range(j*512+128,(j+1)*512):mean[j]+=levels[code[k]]/384
        for k in range(j*512+128,(j+1)*512):var[j]+=(levels[code[k]]-mean[j])**2/384
    return mean,var


def save(path,data):path.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def pareto(rows):
    frontier=[]
    for r in sorted(rows,key=lambda x:-x['weak_sinad']):
        if not any(p['weak_sinad']>=r['weak_sinad'] and p['strong_loss']<=r['strong_loss'] and
                   p['sync_penalty']<=r['sync_penalty'] for p in frontier):frontier.append(r)
    return frontier


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--evaluations',type=int,default=100000)
    ap.add_argument('--shortlist',type=int,default=12)
    ap.add_argument('--captures',type=Path,help='optional actual contiguous Q4/I4 capture directory')
    a=ap.parse_args()
    if a.output.exists():ap.error('use a fresh evidence directory')
    a.output.mkdir(parents=True)
    save(a.output/'protocol.json',dict(configuration_seed=9201,screen=9301,selection=9401,
         final=[9501,9502],stress=9601,evaluations=a.evaluations,
         policy='unique compiled LUT behaviours; reference-only positive calibration; freeze before full-frame finals',
         source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},
         hardware='TX-only LUT8/2KiB/eight slots/two bundles/raw32K/IQ40/unique20/DAC40',
         exclusions='not a global architecture optimum; no new math or RF sensitivity claim'))
    templates=pool();baseline=templates[0]
    raw,frequencies=tone_data();base_mean,base_var=tone_stats(codes(raw,baseline),F.LEVELS)
    # A short screen is not reported as a full PAL/NTSC field evaluation.
    cases=[]
    for standard,cnr,rms in [('PAL',2,3),('NTSC',6,1.5),('PAL',30,3),('NTSC',30,5)]:
        c=V.make_case(standard,9301,cnr,rms,short=True)
        # A fixed active-video/H-sync window for the cheap proxy stage.
        # Full vertical trains are reserved for complete-frame selection/final.
        for key in ('raw','clean','truth','region'):
            c[key]=c[key][65536:81920]
        c['calibration']=V.M.clean_calibration(decode(c['clean'],baseline),c['truth'],3000)
        # Six regular lines plus vertical region; state runs continuously.
        c['reference']=V.waveform_metrics(decode(c['raw'],baseline),c)
        cases.append(c)
    db=sqlite3.connect(a.output/'leaderboard.sqlite')
    db.execute('CREATE TABLE candidates (id TEXT PRIMARY KEY,family TEXT,template INTEGER,formula TEXT,cost TEXT,tone_error REAL,status TEXT,weak_sinad REAL,strong_loss REAL,sync_penalty REAL)')
    rng=np.random.default_rng(9201);seen=set();parents=[];leaders=[]
    frontier=[]
    attempts=count=proxy_count=cost_rejected=duplicates=0;start=time.monotonic()
    while count<a.evaluations:
        attempts+=1
        if attempts>a.evaluations*12:raise RuntimeError('unique behaviour budget exhausted; do not relabel duplicates')
        ti=int(rng.integers(len(templates)));template=templates[ti];tracking=bool(template.get('tracking'))
        if parents and rng.random()<.45:
            parent=parents[int(rng.integers(len(parents)))];ti=parent['template'];template=templates[ti]
            tracking=bool(template.get('tracking'))
            if rng.random()<.3:
                other=parents[int(rng.integers(len(parents)))]
                expr=E.recombine(parent['expression'],other['expression'],rng)
            else:expr=E.mutate(parent['expression'],rng,tracking)
        else:expr=E.generate(rng,tracking)
        # Preserve baseline-anchored variation in half the proposals. This
        # is an explicit compiled teacher component, not an invisible prior.
        if rng.random()<.5:
            expr=dict(op='mix',unit='rad/span',k=float(10**rng.uniform(-3,0)),
                      args=[dict(op='ovp',unit='rad/span'),expr])
        reconstruction='code' if rng.random()<.8 else 'physical'
        try:m,digest,resource=synthesize(template,expr,reconstruction)
        except (ValueError,AssertionError):cost_rejected+=1;continue
        if digest in seen:duplicates+=1;continue
        seen.add(digest);count+=1;m['name']='DSP-'+digest[:12]
        mean,var=tone_stats(codes(raw,m),F.LEVELS)
        tone_error=float(np.max(abs(mean-base_mean)))
        # Signed polarity, fixed absolute clean levels and tone jitter. No
        # candidate-specific fit can turn inverted CVBS into a good score.
        acceptable=(tone_error<=1.25 and np.corrcoef(mean,base_mean)[0,1]>.995 and
                    np.all(var<=base_var*1.25+.15))
        status='tone_rejected';weak=strong=sync=None
        if acceptable:
            proxy_count+=1;rr=[V.waveform_metrics(decode(c['raw'],m),c) for c in cases]
            failures=[reason for r,c in zip(rr,cases) for reason in V.gate(r,c['reference'],c['cnr']>=14)]
            weak=float(np.mean([r['sinad'] for r,c in zip(rr,cases) if c['cnr']<=6]))
            strong=float(max(c['reference']['sinad']-r['sinad'] for r,c in zip(rr,cases) if c['cnr']>=14))
            sync=float(sum(r['h_missing']+r['v_missing'] for r in rr))
            status='proxy_eligible' if not failures else 'proxy_rejected'
            if not failures:
                item=dict(id=digest,model=m,template=ti,expression=expr,weak_sinad=weak,strong_loss=strong,sync_penalty=sync)
                frontier=pareto(frontier+[item])
                parents.append(item);parents.sort(key=lambda x:-x['weak_sinad']);del parents[32:]
                leaders.append(item);leaders.sort(key=lambda x:-x['weak_sinad']);del leaders[max(128,a.shortlist):]
        db.execute('INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (digest,'state4' if tracking else 'pair_context',ti,json.dumps(expr),json.dumps(resource),tone_error,status,weak,strong,sync))
        if count%1000==0:
            db.commit();print('SEARCH',count,'attempts',attempts,'proxy',proxy_count,'eligible',len(parents),'seconds',round(time.monotonic()-start),flush=True)
    db.commit();db.close()
    save(a.output/'pareto.json',[{k:v for k,v in r.items() if k!='model'} for r in frontier])
    finalists=leaders[:a.shortlist]
    # If no candidate survives, retain the known working baseline. Do not
    # weaken gates or select a failed model to manufacture a winner.
    save(a.output/'frozen.json',dict(finalists=[r['model'] for r in finalists],baseline=baseline['name'],no_reselection=True))
    for r in finalists:
        (a.output/(r['model']['name']+'.bsasm')).write_text(H.compile_model(r['model']))
    summary=dict(unique_evaluations=count,attempts=attempts,duplicate_behaviours=duplicates,
                 feasibility_rejected=cost_rejected,tone_tests=count*16,
                 proxy_candidates=proxy_count,proxy_waveform_tests=proxy_count*len(cases),
                 frozen_finalists=len(finalists),elapsed_s=time.monotonic()-start,
                 promotion='none; independent full-frame selection/final confirmation required')
    save(a.output/'summary.json',summary);print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
