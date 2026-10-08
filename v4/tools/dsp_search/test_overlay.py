"""Exhaustive shared-word addressing/state proof; not measured throughput."""
from functools import lru_cache
import numpy as np
import overlay_fsm as O


def main():
    rng=np.random.default_rng(12011);count=0
    # Cache parsing only. Each source execution retains its own registers.
    original=O.H.BS.parse;O.H.BS.parse=lru_cache(maxsize=8)(original)
    try:
        for b in range(2,7):
            p=O.propose(rng);p.update(token_bits=b,phases=4,confidence_groups=1)
            m=O.synthesize(p);source=O.compile_model(m);lut=m['lut'];sb=10-b
            for key,value in [('kp',float('nan')),('ki',8),('radius_scale',0),('error_function','unknown')]:
                broken=dict(p);broken[key]=value
                try:O.synthesize(broken)
                except ValueError:pass
                else:raise AssertionError(('invalid model accepted',key))
            for state in range(1<<sb):
                for raw in range(256):
                    token=lut[768+raw]>>(16-b)
                    expected=lut[(state<<b)+token]&63
                    junk=((1<<b)-1)<<(16-b)
                    out=O.H.BS.simulate(source,[raw,0,raw,0],4,
                                        initial=(0,0,0,(state<<6)|junk),wrap_rom=True)
                    assert [v&63 for v in out]==[0,0,expected,expected],(b,state,raw)
                    count+=1
            raw=rng.integers(0,256,70000,dtype=np.uint8)
            actual=O.H.BS.simulate(source,raw,len(raw),wrap_rom=True)
            assert np.array_equal(np.array(actual)&63,O.codes(raw,np.array(lut,np.uint16),b))
    finally:O.H.BS.parse=original
    # With unity phase correction and a uniform grid, phase-advance output
    # must reduce to the independently defined adjacent phase32 discriminator.
    p=O.propose(rng,extended=True)
    p.update(token_bits=5,phases=32,confidence_groups=1,kp=1,ki=1,
             adaptive=False,rotation=0,error_function='linear',centroid_observations=False,
             reconstruction='quantized_advance')
    m=O.synthesize(p)
    for state in range(32):
        for token in range(32):
            value=m['lut'][state*32+token]
            phase=((token-state)*2*np.pi/32+np.pi)%(2*np.pi)-np.pi
            expected=int(np.rint(np.clip((phase*128/np.pi-O.H.B.P.OFFSET)*O.H.B.P.SCALE,0,63)))
            # Exactly half a turn is the Nyquist sign ambiguity; floating
            # roundoff can select either endpoint. Both represent the same
            # sampled phase. Actual CVBS deviation stays below Nyquist.
            if abs(token-state)==16:assert value&63 in (0,63)
            else:assert value&63==expected
            assert (value>>6)&31==token
    print('PASS overlay:',count,'state/raw combinations including decoder garbage; five continuous70k streams')


if __name__=='__main__':main()
