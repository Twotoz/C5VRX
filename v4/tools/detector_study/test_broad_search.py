#!/usr/bin/env python3
"""Independent schedule and integer-MMSE checks for broad C5VRX search."""
import numpy as np
import broad_search as B


def main():
    rng=np.random.default_rng(1611);count=0
    for layout in B.layouts():
        m=dict(layout,name=f'test-{count}',encoder=B.initialize(layout['current_tokens'],'random-clusters',rng).tolist())
        m['map']=rng.integers(0,64,(layout['previous_tokens'],layout['current_tokens'])).tolist()
        B.source_check(m);count+=1
    # Verify every integer DAC choice against each occupied bin, rather than
    # merely comparing the result with the same mean formula.
    m=dict(next(B.layouts()),encoder=(np.arange(256)%16).tolist())
    w=rng.integers(1,10,(1,256,256));y=rng.uniform(-20,80,w.shape)
    h=(w,w*y,w*y*y);m['map'],loss=B.fit(m,h)
    enc=np.array(m['encoder']);indices=((enc[:,None]>>1)*16+enc[None,:]).ravel()
    for cell,value in enumerate(np.array(m['map']).ravel()):
        mask=indices==cell;ww=w.ravel()[mask];ss=h[1].ravel()[mask]
        costs=np.array([np.sum(ww*v*v-2*ss*v) for v in range(64)])
        assert costs[value]<=costs.min()+1e-6
    # Ported Unwrap75 must reproduce the repository's historical model on the
    # exact same raw stream, including its resistor transfer and cadence.
    import alias_spans as A
    raw=rng.integers(0,256,12000,dtype=np.uint8)
    old=A.raw_bytes;A.raw_bytes=lambda z:raw
    try: expected=A.detect(None,'FIRMWARE')
    finally:A.raw_bytes=old
    assert np.allclose(B.unwrap75(raw),expected,atol=1e-12)
    assert {tuple(l['middle_bits']) for l in B.layouts()}=={(),(3,),(7,),(3,7),(2,6)}
    print(f'PASS {count} architecture source schedules, exhaustive integer bin minima, Unwrap75 reference equivalence')

if __name__=='__main__':main()
