"""Teacher projection changes only finite-state bits, never supplies runtime IQ."""
import numpy as np
import overlay_fsm as O
from distill_overlay import project


def main():
    rng=np.random.default_rng(19117)
    for context in (0,2):
        p=O.propose(rng,True)
        p.update(token_bits=5,phases=32,confidence_groups=1,context_bits=context,
                 context_weight=.2,context_radius=3)
        m=O.synthesize(p);raw=rng.integers(0,256,10000,dtype=np.uint8)
        cases=[dict(raw=raw)];n=len(raw)//2
        targets=[(rng.uniform(-np.pi,np.pi,n),rng.uniform(-3e6,5e6,n),rng.uniform(0,1,n))]
        identity=project(m,cases,targets,0)
        assert identity['lut']==m['lut']
        projected=project(m,cases,targets,.5)
        original=np.array(m['lut'],np.uint16);after=np.array(projected['lut'],np.uint16)
        assert np.array_equal(original&0xf83f,after&0xf83f),'changed DAC or decoder'
        source=O.compile_model(projected)
        actual=O.H.BS.simulate(source,raw,len(raw),wrap_rom=True)
        assert np.array_equal(np.array(actual)&63,O.codes(raw,after,5,context))
    print('PASS teacher projection: identity,isolated state bits,independent compiled execution')


if __name__=='__main__':main()
