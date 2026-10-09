#!/usr/bin/env python3
"""Frozen motion-mixture teacher confirmation; no model selection or refit."""
import argparse,json,hashlib,time
from pathlib import Path
import adaptive_particle as B
from amplitude_cases import cases
from omega_research import controls,measure,summarize

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--particles',type=int,default=2048);a=ap.parse_args()
 if a.output.exists():ap.error('fresh evidence required')
 a.output.mkdir(parents=True);params=dict(particles=a.particles,modes=True)
 (a.output/'config.json').write_text(json.dumps(params,indent=1))
 cfg=B.setup(**params);fns=controls();fns['MOTION PARTICLE']=lambda c:B.decode(c['raw'],cfg,c['fit']['fit_deviation'],c['fit']['fit_centre_hz'])[0]
 rows=[];start=time.time()
 for c in cases(seed=2410190,amplitudes=(3.,7.),cnrs=(2,6,13,30),per=4):
  for name,fn in fns.items():rows.append(dict(seed=c['seed'],cnr=c['cnr'],rms=c['rms'],standard=c['standard'],pattern=c['pattern'],model=name,
    iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(c,fn)))
  (a.output/'confirmation.json').write_text(json.dumps(dict(seed=2410190,params=params,summary=summarize(rows),rows=rows),indent=1))
  print('confirmation',c['cnr'],c['rms'],c['standard'],c['pattern'],round(time.time()-start),flush=True)
 print(json.dumps(summarize(rows),indent=1),flush=True)
if __name__=='__main__':main()
