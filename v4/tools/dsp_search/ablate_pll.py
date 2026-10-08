#!/usr/bin/env python3
"""Offline floating-PLL ablations and traces, never a claimed C5 schedule."""
import argparse
import json
import math
from pathlib import Path
import numpy as np
from numba import njit
import engine as S
import validate_search as Q
import waveforms as V
from video_metrics import waveform,detail


@njit(cache=True)
def trace(raw,p,stride=1,confidence=True,phase_memory=True,frequency_memory=True,
          phase_states=0,frequency_step_hz=0.,reconstruction=0):
    fs=40e6/stride;centre=2*math.pi*1e6/fs;phase=0.;freq=centre
    n=(len(raw)+stride-1)//stride;out=np.zeros(n);phases=np.zeros(n);frequencies=np.zeros(n)
    for k in range(n):
        byte=np.int64(raw[k*stride]);i=byte>>4;q=byte&15
        i=(i-16 if i>7 else i)+.5;q=(q-16 if q>7 else q)+.5
        angle=math.atan2(q,i);old=freq
        predicted=Q.T.wrap(phase+freq);error=Q.T.wrap(angle-predicted)
        error=min(p[3],max(-p[3],error))
        power=i*i+q*q;conf=power/(power+p[2]*p[2]+1e-12) if confidence else 1.
        freq=(1-p[5])*(freq if frequency_memory else centre)+p[5]*centre+p[1]*conf*error
        freq=min(math.pi*.8,max(-math.pi*.8,freq))
        if frequency_step_hz:freq=round(freq*fs/(2*math.pi*frequency_step_hz))*2*math.pi*frequency_step_hz/fs
        phase=Q.T.wrap(predicted+p[0]*conf*error)
        if phase_states:phase=round(phase*phase_states/(2*math.pi))*2*math.pi/phase_states
        out[k]=(freq+p[4]*p[0]*conf*error) if reconstruction==0 else (old+p[0]*conf*error) if reconstruction==1 else freq
        if not phase_memory:phase=angle
        phases[k]=phase;frequencies[k]=freq
    return out,phases,frequencies


def decode(raw,p,**options):
    frequency,_,_=trace(raw,p,**options);stride=options.get('stride',1)
    code=np.rint(np.clip((frequency*256/(np.pi*stride)-S.H.B.P.OFFSET)*S.H.B.P.SCALE,0,63)).astype(int)
    return S.H.B.D.goggle(np.repeat(S.H.B.DAC_VOLTS[code],stride)[:len(raw)])


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    if a.output.exists():ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    hypotheses=json.loads((Q.G.ROOT/'tools/detector_study/models/theory_hypotheses.json').read_text())
    model=next(m for m in hypotheses['winners'] if m['family']=='pll');p=np.array(model['params'])
    variants=[('full',{}),('no-confidence',dict(confidence=False)),('no-phase-memory',dict(phase_memory=False)),
              ('no-frequency-memory',dict(frequency_memory=False)),('pair20-same-gains',dict(stride=2)),
              ('phase-advance-output',dict(reconstruction=1)),('frequency-only-output',dict(reconstruction=2))]
    variants += [('phase'+str(n),dict(phase_states=n)) for n in (8,16,32,64)]
    variants += [('frequency'+str(n)+'Hz',dict(frequency_step_hz=float(n))) for n in (25000,50000,100000,1050000)]
    rows=[];baseline=S.F.load_reference('OVP56');proven=False
    for standard in ('PAL','NTSC'):
        for stressed in (False,True):
            for cnr in (4,8,30):
                c=V.make_case(standard,18123,cnr,3,short=True,stress=stressed,stimulus_seed=118123)
                if not proven:
                    i,q=S.H.B.D.cells(c['raw']);z=i+.5+1j*(q+.5)
                    expected=Q.T.kernel(z,4,p);actual,_,_=trace(c['raw'],p)
                    assert np.allclose(actual,expected,rtol=0,atol=1e-12),'trace differs from floating reference'
                    proven=True
                    out,phase,freq=trace(c['raw'],p)
                    np.savez_compressed(a.output/'training_trace.npz',raw=c['raw'],output=out,phase=phase,frequency=freq)
                for name,options in variants:
                    y=decode(c['raw'],p,**options);r=waveform(y,c)
                    r.update(model=name,standard=standard,stress=stressed,cnr=cnr,detail_corr=detail(y,c))
                    rows.append(r)
                y=S.decode(c['raw'],baseline);r=waveform(y,c)
                r.update(model='OVP56',standard=standard,stress=stressed,cnr=cnr,detail_corr=detail(y,c));rows.append(r)
    Q.write_rows(a.output/'ablation.csv',rows)
    S.save(a.output/'protocol.json',dict(seed=18123,stimulus_seed=118123,params=model['params'],
        variants=variants,trace_matches_reference=True,
        scope='offline ablations; pair20 keeps gains per update and changes physical loop bandwidth; no hardware feasibility or range claim'))
    print('PASS floating trace equivalence;',len(rows),'waveform ablations')


if __name__=='__main__':main()
