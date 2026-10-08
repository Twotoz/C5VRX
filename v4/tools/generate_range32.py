#!/usr/bin/env python3
"""C5VRX pinned first-order RANGE32 tracker; standard-library regeneration."""
import json
import hashlib
import math
from pathlib import Path
from compile_overlay import build

ROOT=Path(__file__).resolve().parents[1]


def tables(p):
    if not (p['token_bits']==5 and p['phases']==32 and p['confidence_groups']==1 and
            p['centroid_observations'] and p['adaptive'] and p['error_function']=='clip' and
            p['reconstruction']=='mixed_advance'):
        raise ValueError('this generator implements only the pinned RANGE32 architecture')
    tau=2*math.pi;rotation=p['rotation'];encoder=[];real=[0.]*32;imag=[0.]*32
    for raw in range(256):
        i,q=raw>>4,raw&15
        i=(i-16 if i>7 else i)+.5;q=(q-16 if q>7 else q)+.5
        token=math.floor((math.atan2(q,i)-rotation)*32/tau+.5)%32
        encoder.append(token);real[token]+=i;imag[token]+=q
    observed=[math.atan2(q,i) if i or q else k*tau/32+rotation
              for k,(i,q) in enumerate(zip(real,imag))]
    lut=[];omega=p['centre_hz']*tau/20e6
    for state in range(32):
        phase=state*tau/32;predicted=phase+omega
        for token in range(32):
            error=(observed[token]-predicted+math.pi)%tau-math.pi
            gain=min(1.8,p['kp']*(1+p['adaptation']*abs(error)/math.pi))
            advance=omega+gain*max(-p['limit'],min(p['limit'],error))
            next_phase=math.floor((phase+advance)*32/tau+.5)%32
            quantized=(next_phase*tau/32-phase+math.pi)%tau-math.pi
            out=(1-p['advance_mix'])*advance+p['advance_mix']*quantized
            code=round(max(0,min(63,(out*128/math.pi+46.08)*(63/117.76))))
            lut.append(code|(next_phase<<6))
    for raw,token in enumerate(encoder):lut[768+raw]|=token<<11
    return lut


def generate():
    model=json.loads((ROOT/'tools/range32_model.json').read_text())
    expected=tables(model['params']);actual=model['lut']
    # Learned DAC6 values are a pinned reconstruction function. The phase
    # recurrence and IQ encoder still have an independent scalar equation.
    if [v&0xffc0 for v in expected]!=[v&0xffc0 for v in actual]:
        raise ValueError('pinned state/encoder differs from equation')
    digest=hashlib.sha256(b''.join(v.to_bytes(2,'little') for v in actual)+bytes([5,0])).hexdigest()
    if model['name']!='OVL-'+digest[:12]:raise ValueError('frozen LUT identity mismatch')
    (ROOT/'firmware/programs/c5vrx4_range32.bsasm').write_text(build(model),encoding='utf-8')
    print('Generated RANGE32 LAB: phase32 first-order, LUT16/2KiB/eight slots/two bundles')


if __name__=='__main__':generate()
