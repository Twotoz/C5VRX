#!/usr/bin/env python3
"""Training-only rare-sync posterior-risk sweep, independent of heldout scores."""
import argparse,json,hashlib
from pathlib import Path
import omega_bayes as B
import omega_student as S
import ladder_edge as LE
import overlay_fsm as O
import compile_overlay as C
from pair_autofit import remap
from omega_research import measure,utility,controls,summarize


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--teacher-config',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    if a.output.exists():ap.error('fresh directory required')
    a.output.mkdir(parents=True); cfg=B.setup(**json.loads(a.teacher_config.read_text()))
    train=LE.cases(610190,per=2,afc=True); seq=[]
    for c in train:
        h,x=B.filter(c['raw'],cfg);seq.append(dict(raw=c['raw'],features=x,hz=h))
    models=[]
    for weight in (2.,8.,32.):
        for bits in (3,4):
            for layout in ('4411','3322'):
                m,fit=S.fit(seq,bits,layout,610190+bits,6,sync_weight=weight)
                fn=lambda c:O.decode(c['raw'],remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
                r=[measure(c,fn) for c in train];score=utility(r);models.append((score,m))
                (a.output/f'w{weight}_b{bits}_{layout}.json').write_text(json.dumps(dict(model=m,fit=fit,training=r,utility=score),indent=1))
                print(weight,bits,layout,score,flush=True)
    score,m=max(models,key=lambda v:v[0]);(a.output/'selected_model.json').write_text(json.dumps(m,indent=1))
    (a.output/'selected.bsasm').write_text(C.build(m))
    fns=controls();fns['OMEGA SYNC RISK']=lambda c:O.decode(c['raw'],remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
    rows=[]
    for seed in (1110190,1210190):
        for c in LE.cases(seed,per=2,afc=True):
            for name,fn in fns.items():rows.append(dict(seed=c['seed'],cnr=c['cnr'],standard=c['standard'],pattern=c['pattern'],iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),model=name,metrics=measure(c,fn)))
    (a.output/'heldout.json').write_text(json.dumps(dict(training_utility=score,summary=summarize(rows),rows=rows),indent=1))
    print('selected',m['params'],score,flush=True)

if __name__=='__main__':main()
