"""Adjoint/timing and immutable state tests for learned DAC reconstruction."""
import numpy as np
import overlay_fsm as O
import reconstruction as R


def main():
    rng=np.random.default_rng(17017)
    for context,counter in ((0,False),(2,False),(0,True),(2,True)):
        p=O.propose(rng,True)
        p.update(token_bits=5,phases=32,confidence_groups=1,context_bits=context,
                 context_weight=.2,context_radius=3,counter_phase=counter,
                 address_bias=511.5 if context else 127.5)
        m=O.synthesize(p);raw=rng.integers(0,256,10000,dtype=np.uint8)
        cases=[dict(raw=raw,cnr=cnr,truth=rng.normal(size=len(raw)),calibration=(lag,2,3))
               for cnr,lag in ((6,-2),(30,3))]
        op,_,_,_=R.operator(m,cases,edge_weight=8)
        x=rng.normal(size=op.shape[1]);y=rng.normal(size=op.shape[0])
        assert np.isclose(np.dot(op.matvec(x),y),np.dot(x,op.rmatvec(y)),rtol=1e-10,atol=1e-9)
        values=rng.uniform(0,63,1024);fitted=R.reconstruct(m,values)
        before=np.array(m['lut'],np.uint16);after=np.array(fitted['lut'],np.uint16)
        assert np.array_equal(before&0xffc0,after&0xffc0)
        a=O.indices(raw,before,5,context,counter);b=O.indices(raw,after,5,context,counter)
        assert np.array_equal(a,b)
        source=O.compile_model(fitted)
        actual=O.H.BS.simulate(source,raw,len(raw),wrap_rom=True)
        assert np.array_equal(np.array(actual)&63,O.codes(raw,after,5,context,counter))
    print('PASS learned DAC: causal filter adjoint/lag,immutable encoder/state,source-driven output')


if __name__=='__main__':main()
