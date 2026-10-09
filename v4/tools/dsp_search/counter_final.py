#!/usr/bin/env python3
"""Frozen final levels/content confirmation. Neither selects nor trains."""
import argparse,json,hashlib
from pathlib import Path
from amplitude_cases import cases
from omega_research import controls,measure
import counter_history as B
import counter_diff as D

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--model',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 if a.output.exists():ap.error('fresh results required')
 m=json.loads(a.model.read_text());fns=controls()
 fns['COUNTER HISTORY']=lambda c:B.decode(c['raw'],B.remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
 fns['COUNTER EXACT128']=lambda c:D.decode(c['raw'],D.synthesize(False,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
 rows=[]
 for c in cases(seed=1910190,amplitudes=(3.,7.),cnrs=(2,6,13,16),per=4):
  for name,fn in fns.items():rows.append(dict(seed=c['seed'],standard=c['standard'],pattern=c['pattern'],cnr=c['cnr'],rms=c['rms'],model=name,
    iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(c,fn)))
  print(c['cnr'],c['rms'],c['standard'],c['pattern'],flush=True)
 a.output.write_text(json.dumps(dict(seed=1910190,model_sha256=hashlib.sha256(a.model.read_bytes()).hexdigest(),rows=rows),indent=1))
if __name__=='__main__':main()
