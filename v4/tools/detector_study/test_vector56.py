#!/usr/bin/env python3
"""C5VRX research: source-driven schedule proof and held-out video benchmarks.
Dataflow model implements established eight-slot wrap; no hardware timing claim.
Independent test seeds differ from codebook training and selection seeds.
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from vector56 import *
import json
best=json.loads((Path(__file__).resolve().parents[1]/'vlp56_codebook.json').read_text());enc,tab=np.array(best['encoder']),np.array(best['map'])
lut=np.zeros(2048,int);lut[1792:]=enc
for prev in range(28):lut[prev*64:prev*64+56]=tab[prev]
s='cfg prefetch true\ncfg eof_on downstream\ncfg trailing_bytes 0\ncfg lut_width_bits 8\nlut '+' '.join(map(str,lut))+'\n'
for n in range(4):
 s+=f'''controller_{n}:
    set 0..5 L0..L5,
    set 8..13 L0..L5,
    set 16..23 0..7,
    set 24..26 H,
    read 16,
    write 16,
    nop
worker_{n}:
    set 16..21 L0..L5,
    set 22..26 B1..B5,
    ldctib
'''
# B operand is old_previous*64 + current6, so B1..B5 is current>>1.
stream=[]
for a in range(256):
 for b in range(256):stream.extend((a,0,b,0))
stats={};o=np.array(V.BS.simulate(s,stream,len(stream),stats=stats,wrap_rom=True)).reshape(-1,2)
t=enc[np.array(stream)[::2]];expected=tab[t[:-1]>>1,t[1:]]
assert np.array_equal(o[2:,0],expected[:len(o)-2]);assert np.array_equal(o[:,0],o[:,1])
assert len(lut)==2048;assert len(V.BS.parse(s)[2])==8
print('PASS all 65536 raw endpoint pairs in continuous stream; 2-bundle schedule; 2048-byte LUT; duplicate DAC')
assert (Path(__file__).resolve().parents[2]/'firmware/programs/c5vrx4_vlp56.bsasm').is_file()

print('UNSEEN SEEDS 309,310,311 (SINAD,clicks per 1000)')
r={n:[] for n in ('adj40','adj40+clamp','HC50','VLP56')}
for seed in (309,310,311):
 sig,noise,_,ire=V.T.make_stream(seed);V.D.truth=V.D.goggle(ire[::2])
 for cnr in (2,4,6,8,14):
  raw=V.T.noisy_raw(sig,noise,cnr)
  ys={'adj40':V.D.DESIGNS['adj40'](raw),'adj40+clamp':V.D.DESIGNS['adj40+clamp'](raw),'HC50':V.H.DESIGNS['hc50p6'](raw),'VLP56':decode(raw,enc,tab)}
  for n,y in ys.items():tot,_,clicks=V.D.score(y);r[n].append((tot,clicks))
for n,a in r.items():print(n,np.array(a).reshape(3,5,2).mean(0).round(2).tolist(),flush=True)
