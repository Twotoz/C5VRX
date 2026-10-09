#!/usr/bin/env python3
"""Frozen independent full-field and disturbance confirmation. Never trains."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import waveforms as V
import overlay_fsm as O
from pair_autofit import remap
from omega_research import controls, measure, summarize
import search_edge as E
from scipy.ndimage import uniform_filter1d


def recovery(y, ref, c, end_us, clean_ref):
    # Same stream and same demod without ONLY the outage. Error to the
    # paired recovered output, with fixed clean calibration. One real line
    # of sustained improvement required, at 0.2-us resolution.
    cc=E.clamp(c,clean_ref); a,t,_=V.calibrated(y,cc);b,_,_=V.calibrated(ref,cc)
    line=2560 if c['standard']=='PAL' else 2542
    er=uniform_filter1d(abs(a-t),line); baseline=uniform_filter1d(abs(b-t),line)
    ok=er<=baseline+3; lo=max(0,int(end_us*40)-max(3000,-cc['calibration'][0]))
    bad=np.r_[0,np.cumsum(~ok)]
    for k in range(lo,len(ok)-line,8):
        if bad[k+line]==bad[k]:return (k-lo)/40
    return None


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--model',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--seed',type=int,default=910190)
    args=ap.parse_args()
    if args.output.exists():ap.error('fresh results path required')
    m=json.loads(args.model.read_text());fns=controls();fns['OMEGA']=lambda c:O.decode(c['raw'],remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
    rng=np.random.default_rng(args.seed);rows=[];k=0
    # Independent complete interlaced fields, including actual H/V intervals,
    # with moving line/field textures/chroma/checkerboard. Not camera captures.
    plans=[dict(standard=s,cnr=n,pattern=p,short=False) for s in ('PAL','NTSC') for n in (3,6,20)
           for p in ('bars','zoneplate','checker','texture','osd')]
    stresses=[dict(stress=True),dict(gain_windows=((1700,1750,.35),(1800,1850,1.8))),
              dict(phase_jumps=((1700,np.pi*.8),)),dict(interferers=((.4,2.7e6,.7),))]
    stresses += [dict(loss_windows_us=((1700,1700+t),)) for t in (2,20,100,500,1000)]
    plans += [dict(standard=s,cnr=n,pattern='checker',short=True,**d) for s in ('PAL','NTSC') for n in (4,16) for d in stresses]
    for p in plans:
        k+=1;dev=float(rng.uniform(.55,1.6));centre=-436e3+dev*1436e3+float(rng.uniform(-8e4,8e4))
        hw=dict(deviation=dev,dc=complex(*rng.uniform(-.3,.3,2)),iq_gain=float(rng.uniform(.9,1.1)),iq_phase_deg=float(rng.uniform(-5,5)))
        c=V.make_case(seed=args.seed+k*97,rms=float(rng.uniform(2.2,4.8)),cfo_hz=centre,stimulus_seed=args.seed+k,
                      lane_model='ultrafine',**hw,**p)
        c['fit']=dict(fit_deviation=dev*float(rng.uniform(.95,1.05)),fit_centre_hz=centre+float(rng.uniform(-5e4,5e4)))
        # Matching no-outage reference retains other transmitter/board faults.
        nofade=None
        if p.get('loss_windows_us'):
            nofade=V.make_case(seed=args.seed+k*97,rms=c['rms'],cfo_hz=centre,stimulus_seed=args.seed+k,
                            lane_model='ultrafine',**hw,**dict(p,loss_windows_us=()))
            nofade['fit']=c['fit']
        for name,fn in fns.items():
            r=measure(c,fn)
            if nofade:r['recovery_us']=recovery(fn(c),fn(nofade),c,p['loss_windows_us'][0][1],fn(dict(nofade,raw=nofade['clean'])))
            rows.append(dict(seed=c['seed'],cnr=c['cnr'],standard=c['standard'],pattern=c['pattern'],model=name,
                        plan=p,hardware={**hw,'dc':[hw['dc'].real,hw['dc'].imag]},metrics=r,
                        iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest()))
        args.output.write_text(json.dumps(dict(seed=args.seed,model_sha256=hashlib.sha256(args.model.read_bytes()).hexdigest(),
                            summary=summarize(rows),rows=rows),indent=1))
        print('confirmed',k,len(plans),p,flush=True)

if __name__=='__main__':main()
