#!/usr/bin/env python3
"""Training-only followup: repair two-bin posterior startup trapping.

Symmetric .995 priors cannot accumulate typical positive likelihood ratios
from the diffuse/low startup state. Test asymmetric predictive prototypes,
not a claim that the earlier scalar-utility winner meets strong gates.
"""
import argparse,json,hashlib
from pathlib import Path
import numpy as np
import counter_bayes as B
from amplitude_cases import cases
from omega_research import measure,utility,controls
import counter_diff as D

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--stats',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 if a.output.exists():ap.error('fresh results required')
 a.output.mkdir(parents=True);stats=json.loads(a.stats.read_text())['stats']
 train=list(cases(seed=1411190,amplitudes=(1.5,3.,4.8,7.)))
 candidates=[];screen=[]
 for prior in ((.2,.8),(.3,.95),(.4,.98),(.4,.9)):
  for mode in ('bayes','strong_anchor'):
   m=B.build(stats,prior,mode=mode);fn=lambda c:B.decode(c['raw'],B.remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
   metrics=[measure(c,fn) for c in train];u=utility(metrics)
   strong=[r for c,r in zip(train,metrics) if c['cnr']>=20]
   anchor=[measure(c,lambda cc:D.decode(cc['raw'],D.synthesize(False,cc['fit']['fit_deviation'],cc['fit']['fit_centre_hz']))) for c in train if c['cnr']>=20]
   loss=np.mean([x['sinad']-y['sinad'] for x,y in zip(anchor,strong)])
   qualifies=loss<=.5 and sum(x['h_missing']+x['v_missing'] for x in strong)<=sum(x['h_missing']+x['v_missing'] for x in anchor)
   name=f'{mode}_{prior[0]}_{prior[1]}';screen.append(dict(name=name,utility=u,strong_loss=float(loss),strong_qualifies=bool(qualifies),metrics=metrics))
   (a.output/(name+'.json')).write_text(json.dumps(m,indent=1));(a.output/(name+'.bsasm')).write_text(B.source(m))
   candidates.append((bool(qualifies),u,m,name));print(name,u,loss,qualifies,flush=True)
 q,u,m,name=max(candidates,key=lambda v:(v[0],v[1]));(a.output/'training.json').write_text(json.dumps(dict(seed=1411190,screen=screen,selected=name,strong_qualifies=q),indent=1))
 (a.output/'selected_model.json').write_text(json.dumps(m,indent=1));(a.output/'selected.bsasm').write_text(B.source(m))
 fns=controls();fns['COUNTER BAYES']=lambda c:B.decode(c['raw'],B.remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
 fns['COUNTER EXACT128']=lambda c:D.decode(c['raw'],D.synthesize(False,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
 rows=[]
 for c in cases(seed=1610190):
  for label,fn in fns.items():rows.append(dict(seed=c['seed'],rms=c['rms'],cnr=c['cnr'],standard=c['standard'],model=label,
    iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(c,fn)))
  print('confirmation',c['cnr'],c['rms'],c['standard'],flush=True)
 (a.output/'confirmation.json').write_text(json.dumps(dict(seed=1610190,selected=name,strong_qualifies=q,rows=rows),indent=1))
if __name__=='__main__':main()
