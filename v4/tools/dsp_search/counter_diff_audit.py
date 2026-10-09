#!/usr/bin/env python3
"""Fresh paired amplitude/counter-architecture screen. Never auto-promotes."""
import argparse,json,hashlib
from pathlib import Path
import numpy as np
from amplitude_cases import cases
import counter_diff as D
from omega_research import measure
import bs_model as BS

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 if a.output.exists():ap.error('fresh evidence required')
 a.output.mkdir(parents=True);rows=[]
 for ctx in (False,True):
  m=D.synthesize(ctx);s=D.source(m);name=m['name']
  raw=np.random.default_rng(2026190).integers(0,256,70000,dtype=np.uint8)
  stats={};actual=BS.simulate(s,raw,len(raw)//2,wrap_rom=True,stats=stats)
  np.testing.assert_array_equal(np.asarray(actual)&63,D.codes(raw,np.array(m['lut'],np.uint16),ctx))
  (a.output/(name.replace(' ','_')+'.bsasm')).write_text(s)
  (a.output/(name.replace(' ','_')+'.json')).write_text(json.dumps(m,indent=1))
  print('source verified',name,stats['bundles'],flush=True)
 for c in cases():
  for ctx in (False,True):
   fn=lambda cc:D.decode(cc['raw'],D.synthesize(ctx,cc['fit']['fit_deviation'],cc['fit']['fit_centre_hz']))
   rows.append(dict(seed=c['seed'],rms=c['rms'],standard=c['standard'],cnr=c['cnr'],model='CONTEXT64' if ctx else 'EXACT128',
    iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(c,fn)))
  print(c['cnr'],c['rms'],c['standard'],flush=True)
 (a.output/'results.json').write_text(json.dumps(dict(seed=1310190,rows=rows),indent=1))
if __name__=='__main__':main()
