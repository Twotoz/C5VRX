"""Reproducible contextual Bayesian ROM control, not a new hardware schedule."""
import argparse,json,time
from pathlib import Path
import numpy as np
import belief_context_rom as B,compile_overlay as C,overlay_fsm as O
from amplitude_cases import cases
from omega_research import measure,utility
from pair_autofit import remap
from search_belief_rom import confirm

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,default=Path('v4/docs/data/belief_context_rom'));a=ap.parse_args()
 out=a.output
 if out.exists():ap.error('fresh evidence required')
 out.mkdir(parents=True);cfg=B.setup();train=list(cases(seed=3210190,amplitudes=(3.,7.),cnrs=(4,20),per=2,size=16384))
 snap=[];start=time.time()
 for c in train:
  snap.append(B.snapshots(c['raw'],cfg));print('teacher',c['cnr'],c['rms'],c['standard'],round(time.time()-start),flush=True)
 snap=np.concatenate(snap);candidates=[];rows=[]
 for bits in (2,3,4):
  m,info=B.fit(snap,cfg,bits)
  metrics=[measure(c,lambda cc:O.decode(cc['raw'],remap(m,cc['fit']['fit_deviation'],cc['fit']['fit_centre_hz']))) for c in train]
  u=utility(metrics);name='b'+str(bits);candidates.append((u,m,name));rows.append({'name':name,'utility':u,'projection':info})
  (out/(name+'.json')).write_text(json.dumps({'model':m,'projection':info,'metrics':metrics},indent=1));(out/(name+'.bsasm')).write_text(C.build(m));print('ROM',name,u,info,flush=True)
 _,m,name=max(candidates,key=lambda x:x[0]);(out/'selected_model.json').write_text(json.dumps(m,indent=1));(out/'selected.bsasm').write_text(C.build(m));(out/'training.json').write_text(json.dumps({'seed':3210190,'selected':name,'screen':rows},indent=1));confirm(m,out)

if __name__=='__main__':main()
