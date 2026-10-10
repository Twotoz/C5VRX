#!/usr/bin/env python3
"""Predeclared likelihood-space encoder / belief-ROM experiment.

No confirmation score selects or modifies the policy. Training may access
future training records to construct fixed ROM parameters, but both reference
and integer hardware inference are causal. Full-address/token references
isolate observation loss from finite-state projection. Not firmware approval.
"""
import argparse,json,time,hashlib
from pathlib import Path
import numpy as np
import belief_rom as B
import compile_overlay as C
import overlay_fsm as O
from amplitude_cases import cases
from omega_research import measure,controls,utility,summarize
from pair_autofit import remap


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--phases',type=int,default=64)
    ap.add_argument('--frequencies',type=int,default=33)
    ap.add_argument('--bits',type=int,nargs='+',default=[3,4,5])
    ap.add_argument('--layouts',nargs='+',default=['4411','3322'])
    ap.add_argument('--projection',choices=['latent','predictive'],default='latent')
    ap.add_argument('--confirm-only',action='store_true')
    a=ap.parse_args()
    if a.confirm_only:
        m=json.loads((a.output/'selected_model.json').read_text())
        confirm(m,a.output);return
    if a.output.exists():ap.error('fresh output required')
    a.output.mkdir(parents=True);start=time.time()
    train=list(cases(seed=2910190,amplitudes=(3.,7.),cnrs=(4,20),per=2,size=16384))
    screen=[];candidates=[]
    for layout in a.layouts:
        cfg=B.setup(layout,phases=a.phases,frequencies=a.frequencies)
        basis=B.predictive_basis(cfg) if a.projection=='predictive' else None
        for bits in a.bits:
            enc=B.encoder(cfg,bits,2910190+bits)
            snapshots=[]
            for c in train:
                _,snap=B.reference(c['raw'],cfg,enc,stride=32)
                snapshots.append(snap)
                print('posterior',layout,bits,c['cnr'],c['rms'],c['standard'],round(time.time()-start),flush=True)
            b=B.acquisition_codebook(np.concatenate(snapshots),cfg,enc,bits,2910190+bits,basis)
            # Bayesian projection uses the stored state's distribution for
            # EVERY word, including words never visited in training traces.
            m,info=B.project(cfg,b,enc,bits,basis);info.pop('posterior')
            m['params'].update(generator='belief_rom',grid_phases=a.phases,
                               grid_frequencies=a.frequencies,training_seed=2910190,
                               projection=a.projection)
            metrics=[measure(c,lambda cc:O.decode(cc['raw'],remap(m,cc['fit']['fit_deviation'],cc['fit']['fit_centre_hz']))) for c in train]
            score=utility(metrics);name=f'b{bits}_{layout}'
            (a.output/(name+'.json')).write_text(json.dumps(dict(model=m,projection=info,metrics=metrics),indent=1))
            (a.output/(name+'.bsasm')).write_text(C.build(m))
            screen.append(dict(name=name,utility=score,projection=info))
            candidates.append((score,name,m))
            print('ROM',name,score,info,round(time.time()-start),flush=True)
    _,name,m=max(candidates,key=lambda x:x[0])
    (a.output/'training.json').write_text(json.dumps(dict(seed=2910190,selected=name,screen=screen,projection=a.projection,grid=dict(phases=a.phases,frequencies=a.frequencies)),indent=1))
    (a.output/'selected_model.json').write_text(json.dumps(m,indent=1))
    (a.output/'selected.bsasm').write_text(C.build(m))
    confirm(m,a.output)


def confirm(m,output):
    import bs_model as BS
    raw=np.random.default_rng(9310190).integers(0,256,70000,dtype=np.uint8)
    np.testing.assert_array_equal(np.asarray(BS.simulate(C.build(m),raw,len(raw),wrap_rom=True))&63,O.model_codes(raw,m))
    rows=[];fns=controls()
    fns['BELIEF ROM']=lambda c:O.decode(c['raw'],remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
    for c in cases(seed=3010190,amplitudes=(1.5,3.,7.),cnrs=(2,6,13,30),per=2):
        for label,fn in fns.items():
            rows.append(dict(seed=c['seed'],cnr=c['cnr'],rms=c['rms'],standard=c['standard'],pattern=c['pattern'],model=label,iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(c,fn)))
        (output/'confirmation.json').write_text(json.dumps(dict(seed=3010190,summary=summarize(rows),rows=rows),indent=1))
        print('confirmation',c['cnr'],c['rms'],c['standard'],flush=True)

if __name__=='__main__':main()
