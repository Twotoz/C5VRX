#!/usr/bin/env python3
"""Train/screen counter-risk FSMs; holdout cannot select or retune a model."""
import argparse,json,hashlib
from pathlib import Path
import numpy as np
import counter_bayes as B
import waveforms as V
from omega_research import controls,measure,utility
from amplitude_cases import cases
import bs_model as BS

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 if a.output.exists():ap.error('fresh evidence required')
 a.output.mkdir(parents=True);train=[]
 for k in range(24):
  seed=1410190+k;cnr=(2,4,6,8,13,20,30)[k%7];rms=(1.5,3,4.8,7)[k%4]
  c=V.make_case(('PAL','NTSC')[k%2],seed,cnr,rms,short=True,cfo_hz=1e6,stimulus_seed=seed+900,
                lane_model='ultrafine',include_traces=True,pattern=('bars','zoneplate','checker','texture','osd')[k%5])
  for key in ('raw','clean','truth','region','rx_signal'):c[key]=c[key][65536:98304]
  c['fit']=dict(fit_deviation=1.,fit_centre_hz=1e6);train.append(c)
 stats,history=B.fit(train);(a.output/'statistics.json').write_text(json.dumps(dict(stats=stats,history=history),indent=1))
 candidates=[];screen=[]
 for prior in (.8,.95,.995):
  for mode in ('bayes','strong_anchor'):
   m=B.build(stats,prior,mode=mode)
   fn=lambda c:B.decode(c['raw'],B.remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
   metrics=[measure(c,fn) for c in train];u=utility(metrics)
   strong=[r for c,r in zip(train,metrics) if c['cnr']>=13]
   name=f'{mode}_{prior}';screen.append(dict(name=name,utility=u,metrics=metrics))
   (a.output/(name+'.json')).write_text(json.dumps(m,indent=1));(a.output/(name+'.bsasm')).write_text(B.source(m))
   candidates.append((u,m,name));print('train',name,u,flush=True)
 u,m,name=max(candidates,key=lambda v:v[0]);(a.output/'training.json').write_text(json.dumps(dict(seed=1410190,screen=screen,selected=name),indent=1))
 (a.output/'selected_model.json').write_text(json.dumps(m,indent=1));(a.output/'selected.bsasm').write_text(B.source(m))
 raw=np.random.default_rng(77719).integers(0,256,70000,dtype=np.uint8)
 actual=BS.simulate(B.source(m),raw,len(raw)//2,wrap_rom=True)
 np.testing.assert_array_equal(np.asarray(actual)&63,B.trace(raw,np.asarray(m['lut'],np.uint16))[0])
 rows=[];fns=controls();fns['COUNTER BAYES']=lambda c:B.decode(c['raw'],B.remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
 for c in cases(seed=1510190):
  for label,fn in fns.items():rows.append(dict(seed=c['seed'],rms=c['rms'],cnr=c['cnr'],standard=c['standard'],model=label,
    iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(c,fn)))
  print('holdout',c['cnr'],c['rms'],c['standard'],flush=True)
 (a.output/'heldout.json').write_text(json.dumps(dict(seed=1510190,selected=name,rows=rows),indent=1))
if __name__=='__main__':main()
