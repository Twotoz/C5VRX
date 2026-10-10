#!/usr/bin/env python3
"""Reproduce frozen selected-model confirmation without retraining."""
import argparse, hashlib, json
from pathlib import Path
from amplitude_cases import cases
from omega_research import controls,measure,summarize
from pair_autofit import remap
from refine_belief_rom import calibration_case
import overlay_fsm as O


def main():
    ap=argparse.ArgumentParser();ap.add_argument('root',type=Path);args=ap.parse_args()
    m=json.loads((args.root/'selected_model.json').read_text())
    old=json.loads((args.root/'confirmation.json').read_text());seed=old['seed']
    label='CLOSED LOOP ROM' if m['params'].get('generator')=='refine_belief_rom' else 'BELIEF ROM'
    fns=controls();fns[label]=lambda c:O.decode(c['raw'],remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
    rows=[]
    for c in cases(seed=seed,amplitudes=(1.5,3.,7.),cnrs=(2,6,13,30),per=2):
        for name,fn in fns.items():
            rows.append(dict(seed=c['seed'],cnr=c['cnr'],rms=c['rms'],standard=c['standard'],pattern=c['pattern'],model=name,iq_sha256=hashlib.sha256(c['raw'].tobytes()).hexdigest(),metrics=measure(calibration_case(c,m) if name==label else c,fn)))
    assert len(rows)==96
    tmp=args.root/'confirmation.tmp';tmp.write_text(json.dumps(dict(seed=seed,summary=summarize(rows),rows=rows),indent=1))
    tmp.replace(args.root/'confirmation.json')
    print(args.root,seed,len(rows))

if __name__=='__main__':main()
