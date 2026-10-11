#!/usr/bin/env python3
"""Host-only full-Q4 adjacent floating tracker for C5VRX by Twotoz/contributors.

Extends the project's adjacent/PLL research (#53-55/#190). This is a numerical
comparison, NOT a C5 implementation or proof of an information upper bound.
It uses both 25-ns samples, floating state, and no second wrap of pair motion.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from numba import njit
import lut_evolve as E


@njit(cache=True)
def codes(raw, kp, origin_gain):
    out=np.zeros(len(raw),np.uint8)
    phase=0.
    pair=0.
    for i in range(len(raw)-2):
        iv=int(raw[i]>>4);qv=int(raw[i]&15)
        if iv>=8:iv-=16
        if qv>=8:qv-=16
        x,y=iv+.5,qv+.5
        error=(np.arctan2(y,x)-phase+np.pi)%(2*np.pi)-np.pi
        gain=origin_gain if x*x+y*y<.64 else kp
        advance=gain*error
        phase=(phase+advance+np.pi)%(2*np.pi)-np.pi
        pair+=advance
        if i%2==1:
            hz=pair/(2*np.pi*50e-9)
            code=int(np.rint(min(63.,max(0.,(hz+6e6)/15e6*63))))
            out[i+1]=code;out[i+2]=code
            pair=0.
    return out


def evaluate(kp,qg,cases):
    rows=[]
    for spec,c,baseline in cases:
        r=E.D.sim.measure(c,codes(c['raw'],kp,qg),codes(c['clean'],kp,qg))
        rows.append(dict(spec,baseline=baseline,picture=r['picture'],sinad=r['sinad']))
    return dict(kp=kp,origin_gain=qg,assessment=E.D.assess(rows),rows=rows)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    protocol=dict(evidence='host-only floating state; no live timing or upper-bound claim',
                  quantizer='same folded raw Q4 as U85Q; both 25 ns samples',
                  seed_offset=1200000,
                  source_sha256={name:hashlib.sha256((E.D.HERE/name).read_bytes()).hexdigest()
                                 for name in ['floating_oracle.py','lut_evolve.py','demod_search.py','sim.py']})
    E.D.save(a.output/'protocol.json',protocol)
    print('Loading full-field oracle screen',flush=True)
    screen=E.load_cases('screen',0,1200000)
    results=[]
    for kp in [.5,.7,.85,1.]:
        for qg in [.25,.5,1.]:
            r=evaluate(kp,qg,screen);results.append(r)
            E.D.save(a.output/'screen.json',results)
            print(kp,qg,r['assessment'],flush=True)
    best=max(results,key=E.key)
    E.D.save(a.output/'frozen.json',dict(kp=best['kp'],origin_gain=best['origin_gain']))
    print('Loading independent oracle final fields',flush=True)
    final=evaluate(best['kp'],best['origin_gain'],E.load_cases('validation',0,1200000))
    E.D.save(a.output/'validation.json',final)
    print(json.dumps(final['assessment']),flush=True)


if __name__=='__main__':main()
