#!/usr/bin/env python3
"""Hard-runtime recurrent policy refinement; teacher labels never use future IQ."""
import argparse,json,hashlib
from pathlib import Path
import adaptive_particle as B
import fsm_sequence_train as F
import compile_overlay as C
import overlay_fsm as O
from amplitude_cases import cases
from omega_research import controls,measure,summarize
from pair_autofit import remap

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--model',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 if a.output.exists():ap.error('fresh evidence required')
 a.output.mkdir(parents=True);m=json.loads(a.model.read_text());cfg=B.setup(particles=2048,modes=True);seq=[]
 for c in cases(seed=2510190,amplitudes=(1.5,3.,7.),cnrs=(4,20),per=2):
  hz,_=B.filter(c['raw'],cfg);seq.append(dict(raw=c['raw'],hz=hz));print('teacher',c['cnr'],c['rms'],c['standard'],flush=True)
 model,stats=F.optimize(seq,m)
 (a.output/'selected_model.json').write_text(json.dumps(model,indent=1));(a.output/'selected.bsasm').write_text(C.build(model));(a.output/'training.json').write_text(json.dumps(stats,indent=1))
 import bs_model as BS,numpy as np
 raw=np.random.default_rng(771291).integers(0,256,70000,dtype=np.uint8)
 np.testing.assert_array_equal(np.asarray(BS.simulate(C.build(model),raw,len(raw),wrap_rom=True))&63,O.model_codes(raw,model))
 rows=[];fns=controls();fns['SEQUENCE FSM']=lambda c:O.decode(c['raw'],remap(model,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
 for c in cases(seed=2810190,amplitudes=(3.,7.),cnrs=(2,6,13,30),per=4):
  for name,fn in fns.items():rows.append(dict(seed=c['seed'],cnr=c['cnr'],rms=c['rms'],standard=c['standard'],pattern=c['pattern'],model=name,iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(c,fn)))
  (a.output/'confirmation.json').write_text(json.dumps(dict(seed=2810190,summary=summarize(rows),rows=rows),indent=1));print('confirmation',c['cnr'],c['rms'],c['standard'],flush=True)
if __name__=='__main__':main()
