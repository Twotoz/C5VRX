#!/usr/bin/env python3
"""Bounded causal teacher qualification; independent cases never choose model."""
import argparse,json,hashlib,time
from pathlib import Path
import numpy as np
import adaptive_particle as B
from amplitude_cases import cases
from omega_research import controls,measure,utility,summarize

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--particles',type=int,default=2048)
    args=ap.parse_args()
    if args.output.exists():ap.error('fresh evidence required')
    args.output.mkdir(parents=True)
    train=list(cases(seed=2210190,amplitudes=(3.,7.),cnrs=(4,20),per=2))
    screen=[];models=[];start=time.time()
    for name,params in [('walk',dict(acceleration=False,innovation=.25,jump=.08)),
                        ('trajectory',dict(acceleration=True,innovation=.08,jump=.06)),
                        ('trajectory_fast',dict(acceleration=True,innovation=.16,jump=.12))]:
        cfg=B.setup(particles=args.particles,**params)
        fn=lambda c:B.decode(c['raw'],cfg,c['fit']['fit_deviation'],c['fit']['fit_centre_hz'])[0]
        rows=[measure(c,fn) for c in train]
        score=utility(rows);screen.append(dict(name=name,config=params,metrics=rows,utility=score))
        models.append((score,name,params))
        (args.output/'training.json').write_text(json.dumps(dict(seed=2210190,particles=args.particles,screen=screen),indent=1))
        print('training',name,score,round(time.time()-start),flush=True)
    _,name,params=max(models,key=lambda x:x[0]);cfg=B.setup(particles=args.particles,**params)
    (args.output/'config.json').write_text(json.dumps(dict(name=name,particles=args.particles,**params),indent=1))
    rows=[];fns=controls();fns['ADAPTIVE PARTICLE']=lambda c:B.decode(c['raw'],cfg,c['fit']['fit_deviation'],c['fit']['fit_centre_hz'])[0]
    for c in cases(seed=2310190,amplitudes=(1.5,3.,7.),cnrs=(2,6,13,30),per=2):
        for label,fn in fns.items():
            rows.append(dict(seed=c['seed'],cnr=c['cnr'],rms=c['rms'],standard=c['standard'],pattern=c['pattern'],model=label,
                             iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(c,fn)))
        report=dict(seed=2310190,configuration=json.loads((args.output/'config.json').read_text()),summary=summarize(rows),rows=rows)
        (args.output/'confirmation.json').write_text(json.dumps(report,indent=1))
        print('confirmation',c['cnr'],c['rms'],c['standard'],round(time.time()-start),flush=True)
    print(json.dumps(summarize(rows),indent=1),flush=True)
if __name__=='__main__':main()
