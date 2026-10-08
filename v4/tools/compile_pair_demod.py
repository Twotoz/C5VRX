#!/usr/bin/env python3
"""Compile a bounded offline C5VRX demod model to the two-bundle TX schedule.

Standard library only; implements pair, midpoint-quadrant and state4 layouts.
No claim that this enumerates every legal BitScrambler architecture.
"""
import argparse,json
from pathlib import Path


def validate(d):
    n=d['current_tokens'];stride=d['stride'];rows=d['previous_tokens']
    assert n in (32,40,48,56) and stride in (32,64)
    assert rows*stride<=1792 and n<=stride and d['span_ns']==50
    e=d['encoder'];assert len(e)==256 and all(type(x)is int and 0<=x<n for x in e)
    stateful=d.get('tracking',False);mid=d.get('midpoint',False);assert not(stateful and mid)
    table=d['packed_map'] if stateful else d['map']
    assert len(table)==rows and all(len(r)==n for r in table)
    assert all(type(x)is int and 0<=x<(256 if stateful else 64) for r in table for x in r)
    if stateful or mid:
        assert n==56 and rows==28 and stride==64 and d['previous_shift']==3
    else:assert rows==(n>>d['previous_shift'])
    return table


def build(d):
    table=validate(d);lut=[0]*2048;n=d['current_tokens'];stride=d['stride']
    for i,row in enumerate(table):lut[i*stride:i*stride+n]=row
    lut[1792:]=d['encoder']
    name=d.get('name','joint pair FM')
    s=f'''# C5VRX by Twotoz and contributors: compiled {name}.
# Q4/I4 RX40 -> raw32K -> TX-only two bundles/50ns -> DAC6 [D,D] TX40.
# LUT8/2048 bytes; generated offline, no per-sample CPU or boundary resets.
# Host dataflow proof is not board timing or RF acceptance.
# First two output pairs after program load are pipeline startup.
cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 8
lut '''+' '.join(map(str,lut))+'\n'
    for k in range(4):
        s+=f'''\ncontroller_{k}:
    set 0..5 L0..L5,
    set 8..13 L0..L5,
    set 16..23 0..7,
    set 24..26 H,
'''
        if d.get('midpoint'):s+='    set 27 11,\n    set 28 15,\n'
        if d.get('tracking'):s+='    set 29..30 L6..L7,\n'
        s+='    read 16,\n    write 16,\n    nop\n'
        s+=f'worker_{k}:\n'
        if d.get('midpoint') or d.get('tracking'):
            s+='    set 16..21 L0..L5,\n'
            s+=('    set 22..23 B11..B12,\n    set 27..28 O27..O28,\n' if d.get('midpoint') else '    set 22..23 O29..O30,\n')
            s+='    set 24..26 B3..B5,\n'
        else:
            bits=5 if stride==32 else 6;shift=d['previous_shift']
            s+=f'    set 16..{15+bits} L0..L{bits-1},\n'
            s+=f'    set {16+bits}..{20+bits} B{shift}..B{shift+4},\n'
        s+='    ldctib\n'
    return s


def main():
    ap=argparse.ArgumentParser();ap.add_argument('model',type=Path);ap.add_argument('output',type=Path);a=ap.parse_args()
    a.output.write_text(build(json.loads(a.model.read_text())))

if __name__=='__main__':main()
