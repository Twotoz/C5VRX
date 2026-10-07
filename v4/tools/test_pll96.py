#!/usr/bin/env python3
"""Exhaustive source-driven PLL96 state/input proof; not hardware timing."""
import json
import math
import random
from pathlib import Path
import generate_pll96 as G
from bs_model import parse, simulate

ROOT = Path(__file__).resolve().parents[1]


def main():
    model = json.loads((ROOT/'tools/pll96_model.json').read_text())
    p = model['params']
    table, encoder = G.tables(p)
    source = G.build(p)
    assert (ROOT/'firmware/programs/c5vrx4_pll96.bsasm').read_text() == source
    cfg,lut,blocks,_ = parse(source)
    assert cfg['lut_width_bits']=='16' and len(lut)==1024 and len(blocks)==8
    assert len(table)==768 and len(encoder)==256
    assert all(0 <= x < 96*64 for x in table)
    assert all(0 <= x < 8 for x in encoder)
    # Independent signed-IQ contract: I is the HIGH nibble, Q the LOW nibble.
    # The rejected board build swapped these and inverted FM/CVBS polarity.
    rotation=p[5]
    for raw in range(256):
        i,q=raw>>4,raw&15
        i=i-16 if i>7 else i
        q=q-16 if q>7 else q
        expected_token=math.floor((math.atan2(q+.5,i+.5)-rotation)*4/math.pi+.5)%8
        assert encoder[raw]==expected_token,(raw,encoder[raw],expected_token)
    assert encoder[0x51]!=encoder[0x15], 'I/Q swap must change phase'
    # Every valid loop state, every byte, and all invalid startup states. Carry
    # the state in the initial lookup result; simulator derives the addresses
    # from the compiled instructions, not from the recurrence used by search.
    for state in range(128):
        for raw in range(256):
            initial = (0,0,0,state<<6)
            actual = simulate(source,[raw,0,raw,0,raw,0],6,initial=initial,wrap_rom=True)
            value = lut[state*8+encoder[raw]]
            expected = [0,0,value & 63,value & 63]
            value = lut[(value>>6)*8+encoder[raw]]
            expected += [value & 63,value & 63]
            assert [x & 63 for x in actual]==expected,(state,raw)
    rng=random.Random(7501)
    raw=[rng.randrange(256) for _ in range(70000)]
    state,expected=0,[0,0]
    for k in range(0,len(raw),2):
        value=table[state*8+encoder[raw[k]]];state=value>>6
        expected.extend([value&63]*2)
    stats={}
    actual=simulate(source,raw,len(raw),stats=stats,wrap_rom=True)
    assert [x&63 for x in actual]==expected[:len(raw)]
    assert stats['bundles']==len(raw)-1
    phases=int(p[6])
    assert len({(x>>6)%phases for x in table})>1
    assert len({(x>>6)//phases for x in table})>1
    print('PASS PLL96: 128 startup states x 256 raw bytes; state continuity, DAC6 duplicates, LUT16/2KiB/eight slots/two bundles')


if __name__=='__main__':main()
