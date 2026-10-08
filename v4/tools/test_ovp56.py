#!/usr/bin/env python3
"""Standard-library exhaustive native OVP56 source regression."""
import random
import bs_model as bs
from compile_pair_demod import build
from generate_ovp56 import load,TARGET,ROOT


def main():
    d=load();source=TARGET.read_text();assert source==build(d)
    cfg,lut,blocks,labels=bs.parse(source)
    assert cfg['lut_width_bits']=='8' and len(lut)==2048 and len(blocks)==8
    assert d['fit']['max_level_error_dac']<=1 and d['fit']['bounded_converged']
    assert d['independent_clean_levels']['max_dac_error']<=1
    enc,table=d['encoder'],d['map'];raw=bytearray()
    for a in range(256):
        for b in range(256):raw.extend((a,random.Random(a*256+b).randrange(256),b,255))
    raw.extend(bytes(8));stats={};out=bs.simulate(source,raw,len(raw),stats=stats,wrap_rom=True)
    assert stats['bundles']==len(out)-1
    assert all(a==b and a<64 for a,b in zip(out[::2],out[1::2]))
    for pair in range(65536):
        a,b=pair>>8,pair&255
        assert out[4*pair+4]==table[enc[a]>>1][enc[b]]
    for initial in (0,0xffff,0xcccc):
        out=bs.simulate(source,raw[:512],512,initial=(0,0,initial,0),wrap_rom=True)
        tokens=[enc[x] for x in raw[:512:2]]
        assert all(out[2*k+4]==table[tokens[k]>>1][tokens[k+1]] for k in range(len(tokens)-2))
    transport=(ROOT/'main/video_transport.c').read_text();pipeline=(ROOT/'firmware/pipeline.c').read_text()
    assert 'case C5VRX4_DEMOD_OVP56: return s_ovp56_program;' in transport
    print('PASS OVP56: all 65536 raw pairs, startup state, LUT8/2KiB, two bundles, duplicate DAC6, native routing; defaults/quarantine covered by C gate tests')

if __name__=='__main__':main()
