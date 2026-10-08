"""Diagnose PLL96 loop/scaling after the signed-IQ correction, seed 9811.

Selection uses fixed OVP56 levels, never a fitted inversion or gain. This
bounded repair study is separate from the frozen architecture experiment.
"""
import json
from pathlib import Path
import numpy as np
import engine as S
import validate_search as Q
import waveforms as V
import generate_pll96 as G


def main():
    baseline=S.F.load_reference('OVP56');rng=np.random.default_rng(9811)
    cases=[]
    for standard in ('PAL','NTSC'):
        c=V.make_case(standard,9811,30,3,short=True)
        c['reference']=V.waveform_metrics(S.decode(c['raw'],baseline),c)
        cases.append(c)
    original=json.loads((G.ROOT/'tools/pll96_model.json').read_text())['params']
    leaders=[]
    for n in range(4096):
        p=original if n==0 else [rng.uniform(.1,1.25),rng.uniform(.1,1.8),rng.uniform(0,1),
             rng.uniform(-4.8e6,-2.35e6),rng.uniform(4.35e6,7.8e6),rng.uniform(0,np.pi/4),int(rng.choice([4,8,16]))]
        table,enc=G.tables(p);lut=np.array(table+enc,np.int64)
        # Coarse screen on 16,384 samples with fixed reference calibration.
        errors=[]
        for c in cases:
            y=Q.H.B.D.goggle(Q.H.B.DAC_VOLTS[Q.phase_loop(c['raw'],lut)])
            a,t,r=V.calibrated(y,c)
            errors.append(float(np.mean((a[:16384]-t[:16384])**2)))
        loss=sum(errors)
        leaders.append((loss,p));leaders.sort(key=lambda x:x[0]);del leaders[32:]
        if n%512==0:print('REPAIR',n,'best_mse',leaders[0][0],flush=True)
    records=[]
    for loss,p in leaders:
        table,enc=G.tables(p);lut=np.array(table+enc,np.int64);rr=[]
        for c in cases:
            stats=V.waveform_metrics(Q.H.B.D.goggle(Q.H.B.DAC_VOLTS[Q.phase_loop(c['raw'],lut)]),c)
            rr.append(dict(standard=c['standard'],metrics=stats,failures=V.gate(stats,c['reference'])))
        records.append(dict(params=p,screen_mse=loss,validation=rr,
                            missing=sum(r['metrics']['h_missing']+r['metrics']['v_missing'] for r in rr)))
    records.sort(key=lambda x:(x['missing'],sum(len(r['failures']) for r in x['validation']),x['screen_mse']))
    Path('pll96-repair-selection.json').write_text(json.dumps(dict(evaluations=4096,seed=9811,leaders=records),indent=2))
    print(json.dumps(records[0]),flush=True)


if __name__=='__main__':main()
