#!/usr/bin/env python3
"""Project received-IQ floating-PLL traces into actual executable finite states.

Teacher traces are training targets only. Deployed/evaluated execution sees
only raw IQ, compiled tokens/state and the learned LUT. DAC fitting targets
true CVBS independently; no teacher signal is injected at evaluation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import ablate_pll as A
import reconstruction as F
import refine_overlay as R
import overlay_fsm as O
import engine as S


def teachers(cases):
    h=json.loads((A.Q.G.ROOT/'tools/detector_study/models/theory_hypotheses.json').read_text())
    p=np.array(next(m for m in h['winners'] if m['family']=='pll')['params'])
    result=[]
    for c in cases:
        _,phase,freq=A.trace(c['raw'],p)
        i,q=O.H.B.D.cells(c['raw'][::2]);power=(i+.5)**2+(q+.5)**2
        confidence=power/(power+p[2]**2+1e-12)
        result.append((phase[::2],freq[::2]*40e6/(2*np.pi),confidence))
    return result


def project(m,cases,targets,mix,iterations=2):
    if m['params'].get('counter_phase'):raise ValueError('counter-delta projection is not implemented')
    p=m['params'];b=p['token_bits'];states=1<<(10-b);phases=p['phases'];nf=states//phases
    levels=np.array([p['centre_hz']]) if nf==1 else np.linspace(p['low_hz'],p['high_hz'],nf)
    lut=np.array(m['lut'],np.uint16);mask=(states-1)<<6
    for _ in range(iterations):
        count=np.zeros(1024);real=count.copy();imag=count.copy();freq=count.copy()
        for c,(phi,omega,confidence) in zip(cases,targets):
            ix=O.model_indices(c['raw'],m,lut);n=len(ix)
            w=confidence[:n].copy();w[:1500]=0 # Exclude unknown capture-start state.
            count+=np.bincount(ix,weights=w,minlength=1024)
            real+=np.bincount(ix,weights=w*np.cos(phi[:n]),minlength=1024)
            imag+=np.bincount(ix,weights=w*np.sin(phi[:n]),minlength=1024)
            freq+=np.bincount(ix,weights=w*omega[:n],minlength=1024)
        old=(lut>>6)&(states-1);prior=old%phases*2*np.pi/phases
        # Pseudocount24 shrinks poorly observed transitions toward their prior.
        alpha=mix*count/(count+24)
        mean=np.angle(real+1j*imag)
        new_phase=np.floor(np.angle((1-alpha)*np.exp(1j*prior)+alpha*np.exp(1j*mean))*phases/(2*np.pi)+.5).astype(int)%phases
        target=np.divide(freq,count,out=levels[old//phases].copy(),where=count>0)
        blended=(1-alpha)*levels[old//phases]+alpha*target
        new_freq=np.argmin(abs(blended[:,None]-levels),axis=1)
        next_state=new_freq*phases+new_phase
        lut=(lut&np.uint16(65535^mask))|(next_state.astype(np.uint16)<<6)
    result=dict(m,lut=lut.tolist(),state_fit=dict(method='confidence-weighted circular teacher projection',
                mix=mix,iterations=iterations,pseudocount=24,teacher_sample='current first sample of pair'))
    O.compile_model(result);return result


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--initial',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--seed-base',type=int,default=19000)
    a=ap.parse_args()
    if a.output.exists():ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    S.save(a.output/'protocol.json',dict(train_seed=a.seed_base+101,screen_seed=a.seed_base+201,
        selection_seed=a.seed_base+301,final_seeds=[a.seed_base+401,a.seed_base+402],stress_seed=a.seed_base+501,
        stimulus_variation=True,selection_envelope=True,teacher_only_in_training=True,
        scope='new independent protocol; prior vetoes remain; no final reselection',
        source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}))
    train=F.dataset(a.seed_base+101);screen=R.prepare(F.dataset(a.seed_base+201));targets=teachers(train)
    initial=json.loads(a.initial.read_text())['finalists'];seen=set();results=[];proposals=fits=0
    for entry in initial:
        base=entry.get('model') or O.synthesize(entry['params'])
        for mix in (0,.25,.5,.75):
            m=project(base,train,targets,mix) if mix else base
            for sw in (8,32):
                values,info=F.fit(m,train,strong_weight=sw,edge_weight=8);fits+=1
                for blend in (.25,.5,1):
                    proposals+=1;fm=F.reconstruct(m,values,blend);key=R.digest(fm)
                    if key in seen:continue
                    seen.add(key);fm['fit']=dict(info,blend=blend,seed=a.seed_base+101,base_id=entry['id'])
                    metrics=R.assess(fm,screen)
                    results.append(dict(id=key,model=fm,params=fm['params'],metrics=metrics))
        print('DISTILL',len(results),'fits',fits,'best',min(R.rank(r['metrics']) for r in results),flush=True)
    results.sort(key=lambda r:R.rank(r['metrics']));chosen=results[:8];topologies=set()
    for entry in chosen:
        p=entry['params'];topologies.add((p['token_bits'],p['phases'],p['confidence_groups'],p.get('context_bits',0)))
    for entry in results[8:]:
        p=entry['params'];top=(p['token_bits'],p['phases'],p['confidence_groups'],p.get('context_bits',0))
        if top not in topologies:chosen.append(entry);topologies.add(top)
        if len(chosen)>=16:break
    S.save(a.output/'frozen.json',dict(finalists=chosen,no_reselection=True))
    S.save(a.output/'leaderboard.json',dict(candidates=results))
    summary=dict(proposals=proposals,duplicates=proposals-len(results),unique_evaluations=len(results),
                 lsmr_fits=fits,finalists=len(chosen))
    S.save(a.output/'summary.json',summary);print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
