"""ADDCTIA phase residue: addressing, carry/wrap boundaries and raw continuity."""
from functools import lru_cache
import numpy as np
import overlay_fsm as O


def main():
    rng=np.random.default_rng(21117);original=O.H.BS.parse
    O.H.BS.parse=lru_cache(maxsize=4)(original);count=0
    try:
        for context in (0,2):
            p=O.propose(rng,True)
            p.update(token_bits=5,phases=32,confidence_groups=1,counter_phase=True,
                     context_bits=context,context_weight=.15,context_radius=3,
                     address_bias=511.5 if context else 127.5)
            m=O.synthesize(p);source=O.compile_model(m);lut=m['lut']
            assert source.count('addctia')==4 and m['cost']['counter_bits']==16
            for phase in range(32):
                for residue in (0,2047):
                    for raw in range(256):
                        for quad in range(4 if context else 1):
                            second=((quad&1)<<7)|((quad>>1)<<3);address=raw+(quad<<8)
                            accumulator=((phase<<11)+residue+address+(31<<11))&65535
                            token=lut[address]>>11;word=lut[(accumulator>>11)*32+token]
                            actual=O.H.BS.simulate(source,[raw,second,raw,second],4,
                                initial=(0,(phase<<11)+residue,0,(31<<6)|0xf800),wrap_rom=True)
                            assert [v&63 for v in actual]==[0,0,word&63,word&63]
                            count+=1
            raw=rng.integers(0,256,70000,dtype=np.uint8);table=np.array(lut,np.uint16)
            actual=O.H.BS.simulate(source,raw,len(raw),wrap_rom=True)
            assert np.array_equal(np.array(actual)&63,O.codes(raw,table,5,context,True))
    finally:O.H.BS.parse=original
    print('PASS counter:',count,'state/raw/context carry-boundary cases; two continuous70k streams')


if __name__=='__main__':main()
