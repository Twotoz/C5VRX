#!/usr/bin/env python3
"""Closed-loop posterior-expectation policy iteration of a real shared ROM.

Re-estimate each state's FULL belief from the histories that the hard FSM
actually visits, then jointly update transitions, DAC and observation tokens.
This repairs the mismatch between a clustered reference belief and the actual
student occupancy. It is approximate policy iteration, not a global optimum.
Only causal teacher posteriors supply learning targets. Video truth ranks
training policies; a fresh independent confirmation never modifies them.
"""
import argparse,json,time,hashlib
from pathlib import Path
import numpy as np
from numba import njit
import belief_rom as B
import compile_overlay as C
import overlay_fsm as O
from omega_student import addresses,trace
from amplitude_cases import cases
from omega_research import measure,utility,controls,summarize
from pair_autofit import remap


@njit(cache=True)
def rollout(raw,enc,tr,bits,context,history=False):
    n=len(raw)//2;old=np.zeros(n,np.int64);observation=np.zeros(n,np.int64);state=0
    if history:
        # Two actual zero-input warmup spans of prefetch=false, then raw A0.
        for _ in range(2):state=tr[state,enc[0]]
    for k in range(n):
        old[k]=state
        if history:
            q=np.int64(raw[2*k]);r=np.int64(raw[2*k-2]) if k else 0
            a=(q>>4)|((q&15)<<4)|(((r>>7)&1)<<8)|(((r>>3)&1)<<9)
        elif context:a=np.int64(raw[2*k])+((state>>(8-bits))<<8)
        else:
            # PAIR4411: I_A4,Q_A4,I_B1,Q_B1, compiler bit order.
            q=np.int64(raw[2*k]);r=np.int64(raw[2*k+1])
            a=(q>>4)|((q&15)<<4)|(((r>>7)&1)<<8)|(((r>>3)&1)<<9)
        observation[k]=a;state=tr[state,enc[a]]
    return old,observation


def adaptive_features(b,cfg,quality=None,frequency_weight=1.):
    """Confidence-conditioned circular/task information bottleneck.

    Strong coherent phase evidence spends memory on fine phase; diffuse
    evidence spends more on frequency distribution/history. Confidence comes
    from the quantization-aware posterior, never phase-difference magnitude.
    This is an offline compression heuristic, not a Kalman gain or optimality
    theorem. Its information loss and all independent gates must be measured.
    """
    P=cfg['phases'];F=len(cfg['freq']);R=len(cfg['regimes'])
    p=b.reshape(-1,R,F,P);phase=p.sum((1,2));frequency=p.sum((1,3))
    angle=2*np.pi*np.arange(P)/P
    moments=np.stack([phase@np.cos(h*angle) for h in range(1,5)]+
                     [phase@np.sin(h*angle) for h in range(1,5)],1)
    coherence=moments[:,0]**2+moments[:,4]**2
    if quality is not None:coherence*=quality
    uncertainty=np.clip(1-coherence,0,1)
    task=np.sqrt(np.maximum(frequency,0))*np.sqrt(frequency_weight*uncertainty[:,None])
    parts=[moments,task]
    if quality is not None:parts.append(np.asarray(quality)[:,None])
    return np.concatenate(parts,1)


def reseed_unused(book,occupied,posterior_samples,geometry=None):
    """Recover unused memory states by farthest posterior insertion.

    Every insertion weakly reduces nearest-codebook Hellinger distortion.
    This does NOT prove improvement of recurrent video quality. State zero is
    reserved for cold startup; no runtime reinitialization or LUT writer exists.
    """
    pool=np.sqrt(posterior_samples) if geometry is None else geometry(posterior_samples)
    centres=np.sqrt(book[occupied]) if geometry is None else geometry(book[occupied])
    if geometry is None:closest=(pool@centres.T).max(1)
    else:closest=((pool**2).sum(1)[:,None]+(centres**2).sum(1)[None,:]-2*pool@centres.T).min(1)
    for s in np.flatnonzero(~occupied):
        if s==0:continue
        i=closest.argmin() if geometry is None else closest.argmax();book[s]=posterior_samples[i]
        if geometry is None:closest=np.maximum(closest,pool@pool[i])
        else:closest=np.minimum(closest,((pool-pool[i])**2).sum(1))
    return book


def reseed_quality(book,occupied,posterior_samples,book_quality,quality_samples,cfg,frequency_weight):
    """Insert matched joint phase/frequency and inferred-quality prototypes."""
    pool=adaptive_features(posterior_samples,cfg,quality_samples,frequency_weight)
    centres=adaptive_features(book[occupied],cfg,book_quality[occupied],frequency_weight)
    closest=((pool**2).sum(1)[:,None]+(centres**2).sum(1)[None,:]-2*pool@centres.T).min(1)
    for s in np.flatnonzero(~occupied):
        if s==0:continue
        i=closest.argmax();book[s]=posterior_samples[i];book_quality[s]=quality_samples[i]
        closest=np.minimum(closest,((pool-pool[i])**2).sum(1))
    return book,book_quality


def basis(cfg):
    R=len(cfg['regimes']);P=cfg['phases'];F=len(cfg['freq'])
    f=np.tile(np.repeat(cfg['freq'],P),R)
    ph=np.tile(2*np.pi*np.arange(P)/P,R*F)
    columns=[]
    for h in (1,2,4):
        for order in (1,2,3,4):
            angle=order*(ph+2*np.pi*h*f/40.)
            columns.extend([np.cos(angle),np.sin(angle)])
    columns.extend([2*f/6.,f*f/36.])
    return np.stack(columns,1).astype(np.float32)


def calibration_case(c,m):
    """Predeclared structural input delay; no noisy-data lag search."""
    if not m['params'].get('history_observation'):return c
    lag,gain,offset=c['calibration']
    return dict(c,calibration=(lag+4,gain,offset))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--initial',type=Path,nargs='+',required=True)
    ap.add_argument('--rounds',type=int,default=5)
    ap.add_argument('--sync-risk-weight',type=float,default=0.)
    ap.add_argument('--train-seed',type=int,default=3310190)
    ap.add_argument('--confirmation-seed',type=int,default=3410190)
    ap.add_argument('--teacher',choices=['grid','particle'],default='grid')
    ap.add_argument('--reseed-unused',action='store_true')
    ap.add_argument('--projection-geometry',choices=['hellinger','adaptive'],default='hellinger')
    ap.add_argument('--teacher-endpoint',choices=['A','B'],default='B')
    ap.add_argument('--history-observation',action='store_true')
    ap.add_argument('--observation-quality',action='store_true',help='offline inferred A/sigma predictive quality, not true C/N')
    ap.add_argument('--frequency-memory-weight',type=float,default=1.)
    ap.add_argument('--joint-quality-reseed',action='store_true')
    a=ap.parse_args()
    if a.observation_quality and (a.teacher!='particle' or a.projection_geometry!='adaptive'):
        ap.error('observation quality requires particle teacher and adaptive geometry')
    if a.joint_quality_reseed and not (a.observation_quality and a.reseed_unused):
        ap.error('joint quality reseeding requires quality and reseeding')
    if a.frequency_memory_weight<=0 or not np.isfinite(a.frequency_memory_weight):
        ap.error('positive finite frequency memory weight required')
    if a.sync_risk_weight<0 or not np.isfinite(a.sync_risk_weight):ap.error('nonnegative finite risk weight required')
    if a.output.exists():ap.error('fresh output required')
    a.output.mkdir(parents=True)
    if a.teacher=='grid':cfg=B.setup()
    else:cfg=dict(regimes=[0],phases=64,freq=np.linspace(-10.,10.,65))
    kernel=basis(cfg)
    train=list(cases(seed=a.train_seed,amplitudes=(3.,7.),cnrs=(4,20),per=2,size=16384))
    targets=[];start=time.time()
    if a.teacher=='particle':
        import adaptive_particle as AP
        particle_cfg=AP.setup(particles=2048,acceleration=False,innovation=.25,jump=.08,modes=False)
    for c in train:
        if a.teacher=='grid':hz,post=B.reference(c['raw'],cfg,stride=4)
        else:hz,particle_features,post=AP.posterior_snapshots(c['raw'],particle_cfg,stride=4,endpoint=a.teacher_endpoint)
        # Snapshot every four spans, beginning at index3. All output/DAC
        # targets retain full rate. No future observation is used by teacher.
        v=1e6+(hz-c['fit']['fit_centre_hz'])/c['fit']['fit_deviation']
        weight=1+a.sync_risk_weight*np.clip((-v-.7e6)/1.2e6,0,1)**2
        quality=particle_features[3::4,18] if a.observation_quality else None
        feature=post@kernel if a.projection_geometry=='hellinger' else adaptive_features(post,cfg,quality,a.frequency_memory_weight)
        targets.append(dict(quality=quality,weight=weight,raw=c['raw'],hz=hz,post=post,feature=feature,
                            addr=addresses(c['raw'],'4411')))
        print('teacher',c['cnr'],c['rms'],c['standard'],round(time.time()-start),flush=True)
    candidates=[];history=[]
    for path in a.initial:
        m=json.loads(path.read_text());m=m.get('model',m)
        if a.history_observation:
            p=dict(m['params']);p.pop('pair_layout',None);p.pop('context_bits',None);p.pop('context_state_bits',None)
            p.update(history_observation=True,extra_input_delay_raw=4)
            m=dict(m,params=p)
        bits=m['params']['token_bits'];T=1<<bits;S=1024//T
        context=bool(m['params'].get('context_state_bits'))
        history_encoder=bool(m['params'].get('history_observation'))
        if m['params'].get('pair_layout')!='4411' and not context and not history_encoder:raise ValueError('unsupported encoder')
        for epoch in range(a.rounds+1):
            fn=lambda c:O.decode(c['raw'],remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
            metrics=[measure(calibration_case(c,m),fn) for c in train];score=utility(metrics)
            name=f'b{bits}_epoch{epoch}'
            (a.output/(name+'.json')).write_text(json.dumps(dict(model=m,metrics=metrics,utility=score),indent=1))
            (a.output/(name+'.bsasm')).write_text(C.build(m))
            candidates.append((score,name,m));history.append(dict(name=name,utility=score))
            print('policy',name,score,round(time.time()-start),flush=True)
            if epoch==a.rounds:break
            lut=np.asarray(m['lut']);enc=lut>>(16-bits)
            tr=((lut>>6)&(S-1)).reshape(S,T)
            book=np.zeros((S,kernel.shape[0]),np.float32)
            word=np.zeros((1024,kernel.shape[0]),np.float32)
            counts=np.zeros(1024);freqsum=np.zeros(1024)
            sample_counts=np.zeros(1024);state_counts=np.zeros(S)
            book_quality=np.zeros(S);word_quality=np.zeros(1024)
            rollouts=[]
            for target in targets:
                old,observation=rollout(target['raw'],enc,tr,bits,context,history_encoder);idx=old*T+enc[observation]
                weight=target['weight'];ww=weight[3::4]
                counts+=np.bincount(idx,weights=weight,minlength=1024)
                freqsum+=np.bincount(idx,weights=weight*target['hz'],minlength=1024)
                sampled=idx[3::4];nextstate=tr.ravel()[sampled]
                np.add.at(book,nextstate,target['post']*ww[:,None])
                # Hellinger Lloyd's conditional mean square-root posterior.
                np.add.at(word,sampled,np.sqrt(target['post'])*ww[:,None])
                state_counts+=np.bincount(nextstate,weights=ww,minlength=S)
                sample_counts+=np.bincount(sampled,weights=ww,minlength=1024)
                if a.observation_quality:
                    book_quality+=np.bincount(nextstate,weights=ww*target['quality'],minlength=S)
                    word_quality+=np.bincount(sampled,weights=ww*target['quality'],minlength=1024)
                rollouts.append((old,idx,observation))
            occupied=state_counts>0
            book[occupied]/=book[occupied].sum(1,keepdims=True)
            book[~occupied]=1/book.shape[1]
            book_quality/=np.maximum(state_counts,1);word_quality/=np.maximum(sample_counts,1)
            if a.reseed_unused:
                pool=np.concatenate([target['post'] for target in targets])
                if a.observation_quality and a.joint_quality_reseed:
                    qs=np.concatenate([target['quality'] for target in targets])
                    book,book_quality=reseed_quality(book,occupied,pool,book_quality,qs,cfg,a.frequency_memory_weight)
                else:
                    geometry=None if a.projection_geometry=='hellinger' else lambda p:adaptive_features(p,cfg)
                    book=reseed_unused(book,occupied,pool,geometry)
                    book_quality[~occupied]=.5
            word/=np.maximum(sample_counts[:,None],1)
            if a.projection_geometry=='hellinger':nxt=(word@np.sqrt(book).T).argmax(1).reshape(S,T)
            else:
                density=word**2;density/=np.maximum(density.sum(1,keepdims=True),1e-30)
                wf=adaptive_features(density,cfg,word_quality if a.observation_quality else None,a.frequency_memory_weight)
                bf=adaptive_features(book,cfg,book_quality if a.observation_quality else None,a.frequency_memory_weight)
                distance=(wf**2).sum(1)[:,None]+(bf**2).sum(1)[None,:]-2*wf@bf.T
                nxt=distance.argmin(1).reshape(S,T)
            # Unseen words retain the former transition, not arbitrary zero.
            nxt.ravel()[sample_counts==0]=tr.ravel()[sample_counts==0]
            dac=(lut&63).copy()
            seen=counts>0;dac[seen]=B.codes(freqsum[seen]/counts[seen])
            costs=np.zeros((1024,T));book_feature=book@kernel if a.projection_geometry=='hellinger' else adaptive_features(book,cfg,book_quality if a.observation_quality else None,a.frequency_memory_weight)
            for target,(old,_,observation) in zip(targets,rollouts):
                aa=observation[3::4];ss=old[3::4]
                truthcode=B.codes(target['hz'][3::4]).astype(float)
                for t in range(T):
                    projected=book_feature[nxt[ss,t]]
                    risk=np.sum((projected-target['feature'])**2,1)
                    risk+=4*((dac[ss*T+t]-truthcode)/63.)**2
                    costs[:,t]+=np.bincount(aa,weights=risk*target['weight'][3::4],minlength=1024)
            touched=costs.sum(1)>0
            enc[touched]=costs[touched].argmin(1)
            lut=dac.astype(np.uint16)|(nxt.ravel().astype(np.uint16)<<6)|(enc.astype(np.uint16)<<(16-bits))
            m=dict(name='CLOSED LOOP BELIEF ROM',params=dict(m['params'],generator='refine_belief_rom',training_seed=a.train_seed,policy_epoch=epoch+1,sync_risk_weight=a.sync_risk_weight,policy_teacher=a.teacher,reseed_unused=a.reseed_unused,projection_geometry=a.projection_geometry,teacher_endpoint=a.teacher_endpoint,observation_quality=a.observation_quality,frequency_memory_weight=a.frequency_memory_weight,joint_quality_reseed=a.joint_quality_reseed),lut=lut.astype(int).tolist())
    _,name,m=max(candidates,key=lambda x:x[0])
    (a.output/'training.json').write_text(json.dumps(dict(seed=a.train_seed,selected=name,screen=history,teacher=a.teacher,teacher_observations='10-bit PAIR4411' if a.teacher=='grid' else 'both full IQ bytes',teacher_endpoint=a.teacher_endpoint,posterior_sampling_stride=4,sync_risk_weight=a.sync_risk_weight,reseed_unused=a.reseed_unused,projection_geometry=a.projection_geometry,history_observation=a.history_observation,observation_quality=a.observation_quality,frequency_memory_weight=a.frequency_memory_weight,joint_quality_reseed=a.joint_quality_reseed),indent=1))
    (a.output/'selected_model.json').write_text(json.dumps(m,indent=1))
    (a.output/'selected.bsasm').write_text(C.build(m))
    import bs_model as BS
    raw=np.random.default_rng(95).integers(0,256,70000,dtype=np.uint8)
    np.testing.assert_array_equal(np.asarray(BS.simulate(C.build(m),raw,len(raw),wrap_rom=True))&63,O.model_codes(raw,m))
    rows=[];fns=controls();fns['CLOSED LOOP ROM']=lambda c:O.decode(c['raw'],remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
    for c in cases(seed=a.confirmation_seed,amplitudes=(1.5,3.,7.),cnrs=(2,6,13,30),per=2):
        for label,fn in fns.items():rows.append(dict(seed=c['seed'],cnr=c['cnr'],rms=c['rms'],standard=c['standard'],pattern=c['pattern'],model=label,iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(calibration_case(c,m) if label=='CLOSED LOOP ROM' else c,fn)))
        (a.output/'confirmation.json').write_text(json.dumps(dict(seed=a.confirmation_seed,summary=summarize(rows),rows=rows),indent=1))
        print('confirmation',c['cnr'],c['rms'],c['standard'],flush=True)

if __name__=='__main__':main()
