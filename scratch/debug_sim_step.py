#!/usr/bin/env python3
"""Debug cycle-by-cycle BitScrambler state and LUT addressing."""

import sys
from pathlib import Path
import numpy as np

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
sys.path.append(str(ROOT / "tools"))
from bs_model import parse

from compare_h_configs import build_and_test_h74, cap2, golden_matrix

r74 = build_and_test_h74()
lut = r74['lut']
lut_str = " ".join(str(int(v)) for v in lut)

# Load assembly from main/c5vrx2_wbfm_candidate_h.bsasm
asm_text = (ROOT / "main/c5vrx2_wbfm_candidate_h.bsasm").read_text()

cfg, lut_parsed, blocks, labels = parse(asm_text)

# Step-by-step trace of first 10 cycles:
out = a = b = look = pos = pc = 0
raw = list(cap2[:20])

def expand(token):
    if '..' not in token: return [token]
    lo, hi = token.split('..')
    prefix = lo[0] if lo[0].isalpha() else ''
    return [prefix+str(i) for i in range(int(lo[len(prefix):]),int(hi[len(prefix):])+1)]

label_names = {v: k for k, v in labels.items()}

for cycle in range(10):
    lbl = label_names.get(pc, f"step_{pc}")
    new = 0; read = write = 0; opcode = ['nop']
    used = set()
    for line in blocks[pc]:
        bits = line.split()
        if bits[0] == 'set':
            dst, src = expand(bits[1]), expand(bits[2])
            if len(src) == 1: src *= len(dst)
            for d,s in zip(dst,src):
                d=int(d)
                used.add(d)
                if s in ('l','h'): v = s == 'h'
                elif s[0] in 'olab':
                    v=({'o':out,'l':look,'a':a,'b':b}[s[0]] >> int(s[1:])) & 1
                else:
                    bit=int(s); ix=pos+bit//8
                    v=(int(raw[ix]) >> (bit%8)) & 1 if ix < len(raw) else 0
                new |= int(v)<<d
        elif bits[0] == 'read': read=int(bits[1])
        elif bits[0] == 'write': write=int(bits[1])
        else: opcode=bits
    
    # Analyze state before updating out
    curr7 = (new >> 16) & 0x7F
    prev_st = (new >> 23) & 0x0F
    addr = (new >> 16) & 2047
    
    # Output written
    written_val = (out & 0x3F) if write else None
    
    print(f"Cycle {cycle:2d} [{lbl:10s}]: pos={pos}, read raw[{pos}]={raw[pos]:02X}")
    print(f"   -> set addr={addr:4d} (curr7={curr7:3d}, prev_st={prev_st:2d})")
    print(f"   -> Even st in O8..O11={(new >> 8)&15:2d}, Odd st in O12..O15={(new >> 12)&15:2d}")
    print(f"   -> Look for next cycle: lut[{addr}] = {lut_parsed[addr]} (dac={lut_parsed[addr]&63}, l67={(lut_parsed[addr]>>6)&3})")
    if written_val is not None:
        print(f"   -> WRITE DAC code = {written_val}")
    print()
    
    out = new
    look = lut_parsed[(out >> 16) & (len(lut_parsed) - 1)]
    pos += read // 8
    next_pc = pc + 1
    if opcode[0] == 'jmp': next_pc = labels[opcode[1]]
    pc = next_pc
