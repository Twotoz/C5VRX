#!/usr/bin/env python3
"""Joint ROM policy distillation of an amplitude-aware causal teacher.

The requested legal state/observation budgets are tested. This is a constrained
compression control, not a claim that clustering is an optimal policy learner.
"""
import argparse,json,time,hashlib
from pathlib import Path
import numpy as np
import adaptive_particle as B
import omega_student as S
import compile_overlay as C
import overlay_fsm as O
from pair_autofit import remap
from amplitude_cases import cases
from omega_research import controls,measure,utility,summarize

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--confirm-only',action='store_true');ap.add_argument('--features',choices=['posterior','predictive'],default='posterior');ap.add_argument('--bits',type=int,nargs='+',default=[2,3,4]);a=ap.parse_args()
 if a.confirm_only:
  m=json.loads((a.output/'selected_model.json').read_text());name=json.loads((a.output/'training.json').read_text())['selected']
  confirm(m,name,a.output);return
 if a.output.exists():ap.error('fresh evidence required')
 a.output.mkdir(parents=True);cfg=B.setup(particles=2048,modes=True)
 train=list(cases(seed=2510190,amplitudes=(1.5,3.,7.),cnrs=(4,20),per=2));seq=[];start=time.time()
 for c in train:
  hz,x=B.filter(c['raw'],cfg)
  # Explicit feature geometry; prior is diffuse and contains no target C/N.
  z=(np.column_stack([x[:,:2],x[:,12:18],hz/6e6,x[:,5]/6.]) if a.features=='predictive' else
     np.column_stack([x[:,:2],x[:,10:12]/6.,x[:,2]/7.,x[:,3]/3.,x[:,4],x[:,5],x[:,6:10],hz/6e6]))
  seq.append(dict(raw=c['raw'],features=z,hz=hz));print('teacher',c['cnr'],c['rms'],c['standard'],round(time.time()-start),flush=True)
 scale=np.array([1,1,2,2,.5,.5,.5,1,.5,.5,.5,.5,2.])
 prior=np.array([0,0,0,0,.65,.6,0,3,.25,.25,.25,.25,1/6])
 if a.features=='predictive':
  scale=np.array([1,1,1,1,1,1,1,1,2,.5]);prior=np.array([0,0,0,0,0,0,0,0,1/6,.5])
 candidates=[];screen=[]
 for bits in a.bits:
  C.cost(bits)
  for layout in ('4411','3322'):
   m,fit=S.fit(seq,bits,layout,2510190+bits,rounds=8,feature_scale=scale,feature_prior=prior)
   m['name']='ADAPTIVE PARTICLE STUDENT';m['params']['teacher']='adaptive_particle_motion';m['params']['distillation_features']=a.features
   fn=lambda c:O.decode(c['raw'],remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
   metrics=[measure(c,fn) for c in train];u=utility(metrics);name=f'b{bits}_{layout}'
   screen.append(dict(name=name,utility=u,metrics=metrics));candidates.append((u,m,name))
   (a.output/(name+'.json')).write_text(json.dumps(dict(model=m,fit=fit,metrics=metrics),indent=1));(a.output/(name+'.bsasm')).write_text(C.build(m))
   print('student',name,u,round(time.time()-start),flush=True)
 _,m,name=max(candidates,key=lambda x:x[0]);(a.output/'training.json').write_text(json.dumps(dict(seed=2510190,features=a.features,bits=a.bits,selected=name,screen=screen),indent=1))
 (a.output/'selected_model.json').write_text(json.dumps(m,indent=1));(a.output/'selected.bsasm').write_text(C.build(m))
 confirm(m,name,a.output)

def confirm(m,name,output):
 if (output/'confirmation.json').exists():raise ValueError('confirmation already exists')
 import bs_model as BS
 r=np.random.default_rng(77729).integers(0,256,70000,dtype=np.uint8)
 np.testing.assert_array_equal(np.asarray(BS.simulate(C.build(m),r,len(r),wrap_rom=True)) & 63,O.model_codes(r,m))
 rows=[];fns=controls();fns['PARTICLE STUDENT']=lambda c:O.decode(c['raw'],remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
 for c in cases(seed=2610190,amplitudes=(3.,7.),cnrs=(2,6,13,30),per=4):
  for label,fn in fns.items():rows.append(dict(seed=c['seed'],cnr=c['cnr'],rms=c['rms'],standard=c['standard'],pattern=c['pattern'],model=label,iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(c,fn)))
  (output/'confirmation.json').write_text(json.dumps(dict(seed=2610190,selected=name,summary=summarize(rows),rows=rows),indent=1))
  print('confirmation',c['cnr'],c['rms'],c['standard'],flush=True)
if __name__=='__main__':main()
