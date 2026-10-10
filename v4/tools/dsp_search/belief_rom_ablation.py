#!/usr/bin/env python3
"""Frozen independent observation/state-loss ablation, not policy selection."""
import argparse,json,hashlib
from pathlib import Path
import numpy as np
import belief_rom as B
import overlay_fsm as O
from amplitude_cases import cases
from omega_research import controls,measure,summarize
from pair_autofit import remap


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--model',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    if a.output.exists():ap.error('fresh evidence required')
    m=json.loads(a.model.read_text());m=m.get('model',m)
    p=m['params'];bits=p['token_bits']
    cfg=B.setup(p['pair_layout'],p['grid_phases'],p['grid_frequencies'])
    enc=np.asarray(m['lut'])>>(16-bits)
    fns=controls()
    fns['ADDRESS REFERENCE']=lambda c:B.decode_reference(c['raw'],cfg,c['fit']['fit_deviation'],c['fit']['fit_centre_hz'])
    fns['TOKEN REFERENCE']=lambda c:B.decode_reference(c['raw'],cfg,c['fit']['fit_deviation'],c['fit']['fit_centre_hz'],enc)
    fns['BELIEF ROM']=lambda c:O.decode(c['raw'],remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
    rows=[]
    for c in cases(seed=3110190,amplitudes=(3.,7.),cnrs=(2,30),per=2,size=16384):
        for label,fn in fns.items():
            rows.append(dict(seed=c['seed'],cnr=c['cnr'],rms=c['rms'],standard=c['standard'],pattern=c['pattern'],model=label,iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(c,fn)))
            print('ablation',c['cnr'],c['rms'],c['standard'],label,flush=True)
        a.output.write_text(json.dumps(dict(seed=3110190,model_sha256=hashlib.sha256(a.model.read_bytes()).hexdigest(),summary=summarize(rows),rows=rows),indent=1))

if __name__=='__main__':main()
