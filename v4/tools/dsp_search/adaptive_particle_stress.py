#!/usr/bin/env python3
"""Frozen folded-amplitude trajectory reference stress (not selected walk)."""
import argparse,json,hashlib
from pathlib import Path
import adaptive_particle as B
from amplitude_cases import cases
from omega_research import controls,measure,summarize

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 if a.output.exists():ap.error('fresh evidence required')
 a.output.parent.mkdir(parents=True,exist_ok=True);cfg=B.setup(particles=2048)
 params=dict(particles=2048,acceleration=True,innovation=.08,jump=.06,modes=False)
 fns=controls();fns['ADAPTIVE PARTICLE']=lambda c:B.decode(c['raw'],cfg,c['fit']['fit_deviation'],c['fit']['fit_centre_hz'])[0];rows=[]
 for c in cases(seed=2710190,amplitudes=(10.,),cnrs=(4,20),per=2):
  for name,fn in fns.items():rows.append(dict(seed=c['seed'],rms=c['rms'],cnr=c['cnr'],standard=c['standard'],model=name,iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(c,fn)))
  a.output.write_text(json.dumps(dict(seed=2710190,configuration=params,note='Fixed default trajectory stress; not the training-selected walk model.',summary=summarize(rows),rows=rows),indent=1));print(c['cnr'],c['standard'],flush=True)
if __name__=='__main__':main()
