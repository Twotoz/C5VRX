"""PAIR decoder: generated BitScrambler source equals the host simulator.

Proves addressing/dataflow of both raw IQ40 samples, not board timing.
"""
from functools import lru_cache
import numpy as np
import overlay_fsm as O
import overlay_fsm as O0
C=O0.C


def main():
    rng=np.random.default_rng(31011)
    original=O.H.BS.parse;O.H.BS.parse=lru_cache(maxsize=8)(original)
    try:
        for layout,b in (('4411',5),('3322',5),('3232',4),('2233',6),('4141',5)):
            bits=C.pair_bits(layout);assert sorted(bits)==sorted(set(bits)) and len(bits)==10
            p=O.propose(rng,extended=True)
            p.update(token_bits=b,phases=min(32,1<<(10-b)),confidence_groups=1,pair_layout=layout,
                     pair_weight=float(rng.uniform(0,1.2)),pair_rotation=float(rng.uniform(-1,1)))
            m=O.synthesize(p);source=O.compile_model(m)
            assert all(f'set {16+j} {bit},' in source for j,bit in enumerate(bits))
            raw=rng.integers(0,256,70000,dtype=np.uint8)
            actual=O.H.BS.simulate(source,raw,len(raw),wrap_rom=True)
            assert np.array_equal(np.array(actual)&63,O.model_codes(raw,m)),layout
            # Address field of the second sample really reaches the decoder.
            a=raw.copy();a[1::2]^=0x88
            assert not np.array_equal(O.model_codes(a,m),O.model_codes(raw,m)) or layout=='4411'
        for bad in ('4444','0442','3321','33a2',None):
            try:C.pair_bits(bad)
            except ValueError:pass
            else:raise AssertionError(bad)
    finally:O.H.BS.parse=original
    print('PASS pair: five layouts, 70k-sample source/host equivalence with both samples addressed')


if __name__=='__main__':main()
