#!/usr/bin/env python3
"""Final bounded endpoint-likelihood family, with fresh frozen confirmation."""
import argparse,json,hashlib
from pathlib import Path
import numpy as np
import counter_quality as B
import counter_diff as D
import waveforms as V
from amplitude_cases import cases
from omega_research import controls,measure,utility
import bs_model as BS

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 if a.output.exists():ap.error('fresh evidence required')
 a.output.mkdir(parents=True);train=[]
 for k in range(28):
  seed=2010190+k;cnr=(2,4,6,8,13,20,30)[k%7];rms=(1.5,3,4.8,7)[k%4]
  c=V.make_case(('PAL','NTSC')[k%2],seed,cnr,rms,short=True,cfo_hz=1e6,stimulus_seed=seed+900,
                lane_model='ultrafine',include_traces=True,pattern=('bars','zoneplate','checker','texture','osd')[k%5])
  for key in ('raw','clean','truth','region','rx_signal'):c[key]=c[key][65536:98304]
  c['fit']=dict(fit_deviation=1.,fit_centre_hz=1e6);train.append(c)
 candidates=[];screen=[]
 for threshold in (1.5,2.5,4.):
  for weight in (10.,100.):
   m,stats=B.fit(train,threshold,weight);fn=lambda c:B.decode(c['raw'],B.remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
   metrics=[measure(c,fn) for c in train];u=utility(metrics)
   strong=[r for c,r in zip(train,metrics) if c['cnr']>=13]
   anchors=[measure(c,lambda cc:D.decode(cc['raw'],D.synthesize(False))) for c in train if c['cnr']>=13]
   loss=np.mean([x['sinad']-y['sinad'] for x,y in zip(anchors,strong)])
   q=loss<=.5 and sum(x['h_missing']+x['v_missing'] for x in strong)<=sum(x['h_missing']+x['v_missing'] for x in anchors)
   name=f'q{threshold}_w{weight}';screen.append(dict(name=name,utility=u,strong_loss=float(loss),strong_qualifies=bool(q),metrics=metrics))
   (a.output/(name+'.json')).write_text(json.dumps(dict(model=m,statistics=stats),indent=1));(a.output/(name+'.bsasm')).write_text(B.source(m))
   candidates.append((bool(q),u,m,name));print('train',name,u,loss,q,flush=True)
 q,u,m,name=max(candidates,key=lambda x:(x[0],x[1]));(a.output/'training.json').write_text(json.dumps(dict(seed=2010190,screen=screen,selected=name,strong_qualifies=q),indent=1))
 (a.output/'selected_model.json').write_text(json.dumps(m,indent=1));(a.output/'selected.bsasm').write_text(B.source(m))
 raw=np.random.default_rng(77725).integers(0,256,70000,dtype=np.uint8)
 np.testing.assert_array_equal(np.asarray(BS.simulate(B.source(m),raw,len(raw)//2,wrap_rom=True))&63,B.trace(raw,np.asarray(m['lut'],np.uint16))[0])
 rows=[];fns=controls();fns['COUNTER ENDPOINT']=lambda c:B.decode(c['raw'],B.remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
 fns['COUNTER EXACT128']=lambda c:D.decode(c['raw'],D.synthesize(False,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
 for c in cases(seed=2110190,amplitudes=(3.,7.),cnrs=(2,6,13,30),per=4):
  for label,fn in fns.items():rows.append(dict(seed=c['seed'],rms=c['rms'],cnr=c['cnr'],standard=c['standard'],pattern=c['pattern'],model=label,
    iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(c,fn)))
  print('confirmation',c['cnr'],c['rms'],c['standard'],flush=True)
 (a.output/'confirmation.json').write_text(json.dumps(dict(seed=2110190,selected=name,strong_qualifies=q,rows=rows),indent=1))
if __name__=='__main__':main()
