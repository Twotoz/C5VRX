#!/usr/bin/env python3
"""Recompute only recovery with clean frozen alignment; verify recorded IQ hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import waveforms as V
import overlay_fsm as O
from pair_autofit import remap
from omega_research import controls
from omega_confirm import recovery

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--model',type=Path,required=True)
    ap.add_argument('--results',type=Path,required=True)
    args=ap.parse_args(); data=json.loads(args.results.read_text())
    assert data['model_sha256']==hashlib.sha256(args.model.read_bytes()).hexdigest()
    m=json.loads(args.model.read_text()); fns=controls()
    fns['OMEGA']=lambda c:O.decode(c['raw'],remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']))
    rng=np.random.default_rng(data['seed'])
    groups={}
    for r in data['rows']:groups.setdefault(r['seed'],[]).append(r)
    for k,rows in enumerate(groups.values(),1):
        p=rows[0]['plan'];dev=float(rng.uniform(.55,1.6))
        centre=-436e3+dev*1436e3+float(rng.uniform(-8e4,8e4))
        hw=dict(deviation=dev,dc=complex(*rng.uniform(-.3,.3,2)),iq_gain=float(rng.uniform(.9,1.1)),iq_phase_deg=float(rng.uniform(-5,5)))
        rms=float(rng.uniform(2.2,4.8))
        fit=dict(fit_deviation=dev*float(rng.uniform(.95,1.05)),fit_centre_hz=centre+float(rng.uniform(-5e4,5e4)))
        for r in rows:r['hardware'].update(rms=rms,cfo_hz=centre);r['fit']=fit
        if not p.get('loss_windows_us'):continue
        kwargs=dict(seed=data['seed']+k*97,rms=rms,cfo_hz=centre,stimulus_seed=data['seed']+k,lane_model='ultrafine',**hw)
        c=V.make_case(**kwargs,**p);nf=V.make_case(**kwargs,**dict(p,loss_windows_us=()))
        c['fit']=nf['fit']=fit
        assert all(r['iq_sha256']==hashlib.sha256(c['raw'].tobytes()).hexdigest() for r in rows)
        for r in rows:
            fn=fns[r['model']]
            r['metrics']['recovery_us']=recovery(fn(c),fn(nf),c,p['loss_windows_us'][0][1],fn(dict(nf,raw=nf['clean'])))
        print('audited recovery',k,flush=True)
    data['recovery_alignment']='clean matched no-outage output; fixed lag/gain for both noisy streams'
    args.results.write_text(json.dumps(data,indent=1))
if __name__=='__main__':main()
