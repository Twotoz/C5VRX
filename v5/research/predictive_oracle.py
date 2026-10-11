#!/usr/bin/env python3
"""Host-only second-order adjacent PLL study for C5VRX/Twotoz/contributors.

Extends the prior Alpha/PLL and floating_oracle.py research. Not a live C5
implementation; no claim of measured sensitivity or a mathematical optimum.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from numba import njit
import lut_evolve as E


@njit(cache=True)
def codes(raw,kp,beta,output_frequency):
    out=np.zeros(len(raw),np.uint8)
    phase=0.;omega=-2*np.pi*1e6/40e6;pair=0.
    for i in range(len(raw)-2):
        iv=int(raw[i]>>4);qv=int(raw[i]&15)
        if iv>=8:iv-=16
        if qv>=8:qv-=16
        x,y=iv+.5,qv+.5
        low=x*x+y*y<.64
        gain=.5 if low else kp
        predicted=phase+omega
        error=(np.arctan2(y,x)-predicted+np.pi)%(2*np.pi)-np.pi
        advance=omega+gain*error
        phase=(phase+advance+np.pi)%(2*np.pi)-np.pi
        omega+=beta*(.5 if low else 1.)*error
        omega=max(-np.pi,min(np.pi,omega))
        pair+=omega if output_frequency else advance
        if i%2==1:
            hz=pair/(2*np.pi*50e-9)
            code=int(np.rint(min(63.,max(0.,(hz+6e6)/15e6*63))))
            out[i+1]=code;out[i+2]=code;pair=0.
    return out


def evaluate(params,cases):
    rows=[]
    for spec,c,baseline in cases:
        r=E.D.sim.measure(c,codes(c['raw'],**params),codes(c['clean'],**params))
        rows.append(dict(spec,baseline=baseline,picture=r['picture'],sinad=r['sinad']))
    return dict(params=params,assessment=E.D.assess(rows),rows=rows)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    E.D.save(a.output/'protocol.json',dict(seed_offset=1500000,
        evidence='host-only floating phase/frequency; actual folded Q4; independent final seeds',
        source_sha256={name:hashlib.sha256((E.D.HERE/name).read_bytes()).hexdigest()
                       for name in ['predictive_oracle.py','lut_evolve.py','demod_search.py','sim.py']}))
    print('Loading predictive oracle screen',flush=True)
    screen=E.load_cases('screen',0,1500000);results=[]
    for kp in [.55,.7,.85]:
        for beta in [.05,.15,.3]:
            for frequency in [False,True]:
                params=dict(kp=kp,beta=beta,output_frequency=frequency)
                r=evaluate(params,screen);results.append(r)
                E.D.save(a.output/'screen.json',results)
                print(params,r['assessment'],flush=True)
    frozen=[r['params'] for r in sorted(results,key=E.key,reverse=True)[:3]]
    E.D.save(a.output/'frozen.json',frozen)
    print('Frozen three predictors; loading independent final fields',flush=True)
    cases=E.load_cases('validation',0,1500000);checked=[]
    for params in frozen:
        r=evaluate(params,cases);checked.append(r)
        E.D.save(a.output/'validation.json',checked)
        print(params,r['assessment'],flush=True)


if __name__=='__main__':main()
