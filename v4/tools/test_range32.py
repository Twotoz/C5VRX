#!/usr/bin/env python3
"""Pinned scalar equation, every state/input, and continuous BitScrambler proof."""
from functools import lru_cache
import hashlib
import json
import math
import random
import bs_model as B
from generate_range32 import ROOT,tables
from compile_overlay import build


def main():
    m=json.loads((ROOT/'tools/range32_model.json').read_text())
    lut=m['lut'];source=build(m)
    assert [v&0xffc0 for v in tables(m['params'])]==[v&0xffc0 for v in lut],'scalar state/encoder equation differs from search'
    digest=hashlib.sha256(b''.join(v.to_bytes(2,'little') for v in lut)+bytes([5,0])).hexdigest()
    assert m['name']=='OVL-'+digest[:12]
    assert (ROOT/'firmware/programs/c5vrx4_range32.bsasm').read_text()==source
    cfg,_,blocks,_=B.parse(source)
    assert cfg['lut_width_bits']=='16' and len(blocks)==8
    B.parse=lru_cache(maxsize=2)(B.parse)
    for state in range(32):
        for raw in range(256):
            token=lut[768+raw]>>11;word=lut[state*32+token]
            # High decoder bits must not leak into persistent state or DAC.
            actual=B.simulate(source,[raw,0,raw,0],4,initial=(0,0,0,(state<<6)|0xf800),wrap_rom=True)
            assert [v&63 for v in actual]==[0,0,word&63,word&63]
    rng=random.Random(15591);raw=[rng.randrange(256) for _ in range(70000)]
    state=0;expected=[0,0]
    for k in range(0,len(raw),2):
        word=lut[state*32+(lut[768+raw[k]]>>11)];state=(word>>6)&31
        expected.extend([word&63]*2)
    stats={};actual=B.simulate(source,raw,len(raw),stats=stats,wrap_rom=True)
    assert [v&63 for v in actual]==expected[:len(raw)]
    assert stats['bundles']==len(raw)-1
    # Independent physical phase ramp checks FM sign and absolute transfer.
    means=[]
    for hz in (-2e6,0,2e6,4e6):
        raw=[]
        for k in range(4000):
            angle=2*math.pi*hz*k/40e6
            i=math.floor(4*math.cos(angle));q=math.floor(4*math.sin(angle))
            raw.append(((i&15)<<4)|(q&15))
        out=B.simulate(source,raw,len(raw),wrap_rom=True)
        mean=sum(v&63 for v in out[1000:])/len(out[1000:]);means.append(mean)
        expected=(hz*256/20e6+46.08)*63/117.76
        assert abs(mean-expected)<1.5,(hz,mean,expected)
    assert means==sorted(means),'FM polarity inversion'
    print('PASS RANGE32: scalar equation,8192 state/raw cases,continuous70k including DMA wraps,DAC6 duplicates')


if __name__=='__main__':main()
