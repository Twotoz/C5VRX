"""Reproducible hardware-first tracker search for usable weak video.

Unlike the original phase-formula round, this evolves actual recursive LUT16
state transitions and observation-confidence representations. Strong usable
quality limits are frozen before search. No physical sensitivity claim.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import numpy as np
import engine as S
import overlay_fsm as O
import waveforms as V
from video_metrics import detail,waveform,relative_quality


def quality(r,ref):
    failures=[]
    if not r['polarity_ok'] or not 85<=r['contrast']<=115:failures.append('contrast')
    if r['sinad']<max(14,ref['sinad']-1):failures.append('strong_detail')
    if r['h_missing'] or r['v_missing'] or r['v_trains']!=ref['v_trains']:failures.append('sync')
    for k,limit in [('h_jitter_us',.35),('v_jitter_us',.25),('h_width_rmse_us',.15)]:
        if r[k]>max(limit,ref[k]+.1):failures.append(k)
    for k in ('sync_error_ire','black_error_ire','white_error_ire'):
        if abs(r[k])>max(5,abs(ref[k])+2):failures.append(k)
    return failures


def usability(r):
    return (r['polarity_ok'] and 65<=r['contrast']<=120 and r['sinad']>=8 and
            r['h_missing']<=int(.01*r.get('h_expected',0)) and r.get('max_h_gap',0)<=2 and
            r['v_missing']==0 and r['v_trains']==2 and
            r['h_jitter_us']<=.35 and r['v_jitter_us']<=.35 and r['h_width_rmse_us']<=.25)


def mutate(p,rng):
    q=copy.deepcopy(p);fresh=O.propose(rng,extended='reconstruction' in p)
    keys=['kp','ki','low_hz','high_hz','centre_hz','rotation','radius_scale','confidence_floor',
          'error_function','limit','adaptive','adaptation','output_gain','confidence_output']
    if 'reconstruction' in p:keys+=['centroid_observations','reconstruction','advance_mix']
    for k in rng.choice(keys,size=int(rng.integers(1,5)),replace=False):q[str(k)]=fresh[str(k)]
    return q


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--evaluations',type=int,default=400000)
    ap.add_argument('--seed-base',type=int,default=12000)
    ap.add_argument('--detail-gate',action='store_true')
    ap.add_argument('--robust-envelope',action='store_true')
    ap.add_argument('--channel-gate',action='store_true',help='train on separate echo/fade proxies and reject weak channel regressions')
    a=ap.parse_args()
    if a.output.exists():ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True);rng=np.random.default_rng(a.seed_base+101)
    S.save(a.output/'protocol.json',dict(config_seed=a.seed_base+101,screen_seed=a.seed_base+201,
         selection_seed=a.seed_base+301,final_seeds=[a.seed_base+401,a.seed_base+402],stress_seed=a.seed_base+501,evaluations=a.evaluations,
         proxy_detail_gate=a.detail_gate,
         robust_envelope=a.robust_envelope,
         channel_gate=a.channel_gate,
         objective='weak waveform information and pulse recovery subject to strong usable-quality gates',
         strong_quality='>=14dB SINAD, <=1dB OVP loss, signed contrast85..115%, no missing sync; bounded levels/width/jitter',
         caveat='synthetic C/N, not input dBm or physical range; thresholds are engineering criteria',
         source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}))
    baseline=S.F.load_reference('OVP56');tone,_=S.tone_data()
    if a.robust_envelope:
        t=np.arange(512)/40e6
        tone=np.concatenate([O.H.B.D.raw_bytes(radius*np.exp(2j*np.pi*f*t)).astype(np.uint8)
             for radius in (1.5,3,5) for f in (-2.85e6,-1e6,-.435714e6,0,1e6,2e6,3e6,4.85e6)])
    base_mean,base_var=S.tone_stats(S.codes(tone,baseline),S.F.LEVELS)
    cases=[]
    for standard in ('PAL','NTSC'):
        for cnr in (4,8,30):
            c=V.make_case(standard,a.seed_base+201,cnr,3,short=True)
            for key in ('raw','clean','truth','region'):c[key]=c[key][65536:81920]
            c['calibration']=V.M.clean_calibration(S.decode(c['clean'],baseline),c['truth'],3000)
            y=S.decode(c['raw'],baseline)
            c['reference']=waveform(y,c);c['reference_detail']=detail(y,c);cases.append(c)
    if a.robust_envelope:
        for standard,rms,cfo in [('PAL',1.5,1e6),('NTSC',5,1e6),('PAL',3,.5e6),('NTSC',3,1.5e6)]:
            c=V.make_case(standard,a.seed_base+201,30,rms,short=True,cfo_hz=cfo)
            for key in ('raw','clean','truth','region'):c[key]=c[key][65536:81920]
            c['calibration']=V.M.clean_calibration(S.decode(c['clean'],baseline),c['truth'],3000)
            y=S.decode(c['raw'],baseline);c['reference']=waveform(y,c);c['reference_detail']=detail(y,c)
            c['relative_quality']=rms<2
            cases.append(c)
    if a.channel_gate:
        for standard in ('PAL','NTSC'):
            for cnr in (4,8,30):
                c=V.make_case(standard,a.seed_base+201,cnr,3,short=True,stress=True)
                for key in ('raw','clean','truth','region'):c[key]=c[key][65536:81920]
                c['calibration']=V.M.clean_calibration(S.decode(c['clean'],baseline),c['truth'],3000)
                y=S.decode(c['raw'],baseline)
                c['reference']=waveform(y,c);c['reference_detail']=detail(y,c)
                c['relative_quality']=True;cases.append(c)
    db=sqlite3.connect(a.output/'leaderboard.sqlite')
    db.execute('CREATE TABLE candidates (id TEXT PRIMARY KEY,params TEXT,status TEXT,tone_error REAL,score REAL,weak_sinad REAL,weak_missing INTEGER,strong_sinad REAL)')
    seen=set();parents=[];leaders=[];count=attempts=tone_pass=strong_pass=0;start=time.monotonic()
    while count<a.evaluations:
        attempts+=1
        if attempts>a.evaluations*15:raise RuntimeError('unique behaviour budget exhausted')
        p=mutate(parents[int(rng.integers(len(parents)))]['params'],rng) if parents and rng.random()<.65 else O.propose(rng,extended=a.robust_envelope)
        model=O.synthesize(p);lut=np.array(model['lut'],np.uint16)
        digest=hashlib.sha256(lut.tobytes()+bytes([p['token_bits']])).hexdigest()
        if digest in seen:continue
        seen.add(digest);count+=1;status='tone_rejected';score=weak=strong=None;missing=None
        mean,var=S.tone_stats(O.codes(tone,lut,p['token_bits']),S.F.LEVELS)
        error=float(np.max(abs(mean-base_mean)))
        if error<=1.25 and np.corrcoef(mean,base_mean)[0,1]>.995:
            tone_pass+=1;rr=[waveform(O.decode(c['raw'],model),c) for c in cases if c['cnr']==30]
            fail=[f for r,c in zip(rr,[c for c in cases if c['cnr']==30]) for f in
                  (relative_quality(r,c['reference']) if c.get('relative_quality') else quality(r,c['reference']))]
            if not fail and a.detail_gate:
                for c in [c for c in cases if c['cnr']==30]:
                    if detail(O.decode(c['raw'],model),c)<c['reference_detail']-.03:fail.append('fine_detail')
            status='strong_rejected';strong=float(np.mean([r['sinad'] for r in rr]))
            if not fail:
                strong_pass+=1;rr=[V.waveform_metrics(O.decode(c['raw'],model),c) for c in cases if c['cnr']<30]
                weak=float(np.mean([r['sinad'] for r in rr]));missing=int(sum(r['h_missing']+r['v_missing'] for r in rr))
                score=weak-1.5*missing
                item=dict(id=digest,params=p,score=score,weak_sinad=weak,weak_missing=missing,strong_sinad=strong)
                weak_cases=[c for c in cases if c['cnr']<30]
                regression=False
                if a.channel_gate:
                    for stressed in (False,True):
                        group=[(r,c['reference']) for r,c in zip(rr,weak_cases) if c['stress']==stressed]
                        regression |= (np.mean([r['sinad']-ref['sinad'] for r,ref in group])<-.05 or
                            sum(r['h_missing']+r['v_missing'] for r,ref in group)>
                            sum(ref['h_missing']+ref['v_missing'] for r,ref in group))
                if regression:status='weak_channel_rejected'
                else:
                    parents.append(item);parents.sort(key=lambda x:-x['score']);del parents[48:]
                    leaders.append(item);leaders.sort(key=lambda x:-x['score']);del leaders[64:]
                    status='proxy_eligible'
        db.execute('INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?)',(digest,json.dumps(p),status,error,score,weak,missing,strong))
        if count%2000==0:
            db.commit();print('OVERLAY',count,'attempts',attempts,'tone',tone_pass,'strong',strong_pass,'seconds',round(time.monotonic()-start),flush=True)
    db.commit();db.close()
    # Include topology diversity as well as the best score; freeze once.
    chosen=leaders[:16];topologies={(r['params']['token_bits'],r['params']['confidence_groups'],r['params']['phases']) for r in chosen}
    for r in leaders[16:]:
        topology=(r['params']['token_bits'],r['params']['confidence_groups'],r['params']['phases'])
        if topology not in topologies:chosen.append(r);topologies.add(topology)
        if len(chosen)>=24:break
    S.save(a.output/'frozen.json',dict(finalists=chosen,no_reselection=True))
    for r in chosen:
        model=O.synthesize(r['params']);(a.output/(r['id'][:12]+'.bsasm')).write_text(O.compile_model(model))
    result=dict(unique_evaluations=count,proposals=attempts,duplicates=attempts-count,tone_pass=tone_pass,
                strong_pass=strong_pass,finalists=len(chosen),elapsed_s=time.monotonic()-start)
    S.save(a.output/'summary.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':main()
