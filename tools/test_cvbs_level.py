#!/usr/bin/env python3
"""Golden voltage port: actual LUT observer and patched two-slot oracle."""
import ctypes as C
import importlib.util
import random
import re
import subprocess
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class Stats(C.Structure):
    _fields_=[('valid',C.c_bool),('repeated',C.c_uint),('period',C.c_uint),
              ('origin_pm',C.c_uint),('clip_pm',C.c_uint),('sync_uv',C.c_int),
              ('blank_uv',C.c_int),('sync_mad_uv',C.c_int),('blank_mad_uv',C.c_int)]
asm=(ROOT/'main/fm.bsasm').read_text()
lut=[int(x) for x in re.search(r'^lut (.*)$',asm,re.M).group(1).split()]
# Fixed-radius phase representatives, excluding ADC rails / near origin.
representatives={}
for raw in range(256):
    i,q=raw>>4,raw&15
    i=i if i<8 else i-16; q=q if q<8 else q-16
    if i in (-8,7) or q in (-8,7): continue
    radius=(2*i+1)**2+(2*q+1)**2
    if radius<32: continue
    phase=(lut[raw]>>8)&31
    if phase not in representatives or abs(radius-100)<representatives[phase][0]:
        representatives[phase]=(abs(radius-100),raw)
assert len(representatives)==32
with tempfile.TemporaryDirectory() as tmp:
    libpath=Path(tmp)/'level.so'
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-shared','-fPIC',
                    '-Imain','main/cvbs_level.c','-o',str(libpath)],cwd=ROOT,check=True)
    lib=C.CDLL(str(libpath))
    lib.cvbs_level_analyze.argtypes=[C.POINTER(C.c_uint8),C.c_size_t,C.POINTER(C.c_uint16),C.POINTER(Stats)]
    table=(C.c_uint16*1024)(*lut[:1024])
    def analyze(raw):
        data=(C.c_uint8*len(raw))(*raw); out=Stats()
        lib.cvbs_level_analyze(data,len(raw),table,C.byref(out)); return out
    for period in (1271,1280):
        for offset in (0,173,900):
            raw=[]; phase=0
            for k in range(4096):
                position=(k+offset)%period
                step=-1 if position<94 else 1 if position<120 else 2
                phase=(phase+step)&31
                raw.extend([0,representatives[phase][1]])
            v=analyze(raw)
            assert v.valid and v.repeated and v.period==period, (period,offset,v.valid,v.period)
            assert 100000<=v.blank_uv-v.sync_uv<=600000
    assert not analyze(bytes(8192)).valid
    rng=random.Random(31)
    assert not analyze(bytes(rng.randrange(256) for _ in range(8192))).valid
    assert not analyze(bytes(4000)).valid
spec=importlib.util.spec_from_file_location('bs_model',ROOT/'legacy/c5vrx2/tools/bs_model.py')
model=importlib.util.module_from_spec(spec); spec.loader.exec_module(model)
# Every raw endpoint pair preserves its phase decoder and receives exactly the
# selected output map, including addresses overlapping raw decoder entries.
mapping=[(c*11)%64 for c in range(64)]
patched=[(word&~63)|mapping[word&63] for word in lut]
for word,next_word in zip(lut,patched): assert word&~63==next_word&~63
for a in range(256):
    for b in range(256):
        index=(((lut[a]>>8)&31)<<5)|((lut[b]>>8)&31)
        assert patched[index]&63==mapping[lut[index]&63]
patched_asm=re.sub(r'^lut .*$', 'lut '+' '.join(map(str,patched)),asm,flags=re.M)
for seed in range(16):
    rng=random.Random(seed)
    raw=bytes(rng.randrange(256) for _ in range(512))
    observed=model.simulate(patched_asm,raw,512)
    for pair in range(2,256):
        a,b=raw[2*pair-3],raw[2*pair-1]
        index=(((lut[a]>>8)&31)<<5)|((lut[b]>>8)&31)
        code=mapping[lut[index]&63]
        assert observed[2*pair:2*pair+2]==[code,code]
print('PASS: PAL/NTSC actual-Golden observer, noise refusal, 65,536 pair remaps and continuous two-slot output')
