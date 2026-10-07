#!/usr/bin/env python3
"""Meaningful MMSE, coordinate loss and source/compiler regression checks."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import bs_model as BS
from compile_pair_demod import build,validate
import optimize_pair as P
import optimize_video as Q


def main():
    rng=np.random.default_rng(931);w=rng.integers(1,10,(256,256)).astype(float)
    y=rng.uniform(8,55,(256,256));h=(w,w*y,w*y*y)
    enc=P.initialize(32);tab,_=P.fit(enc,32,h)
    baseline=P.empirical_loss(enc,32,tab,h)
    for direction in (-1,1):assert P.empirical_loss(enc,32,np.clip(tab+direction,0,63),h)>baseline
    e,t,trace=P.optimize(enc,32,h,iterations=2)
    assert trace[-1]['loss']<=baseline
    # The filtered-video solver must use the true adjoint, not an inverse IIR.
    from scipy import signal as sg
    x=rng.normal(size=1000);y=rng.normal(size=2000)
    adj=sg.lfilter(*P.D.GOG,y[::-1])[::-1].reshape(-1,2).sum(1)
    assert abs(np.dot(P.D.goggle(np.repeat(x,2)),y)-np.dot(x,adj))<1e-8
    for mode in ('pair','midpoint','tracking'):
        m=dict(name='compiler test',span_ns=50,current_tokens=56,previous_tokens=28,stride=64,previous_shift=1,encoder=(np.arange(256)%56).tolist())
        tab=rng.integers(0,64,(28,56));m['map']=tab.tolist()
        if mode!='pair':m[mode]=True;m['previous_shift']=3
        if mode=='tracking':m['packed_map']=(tab+64*rng.integers(0,4,(28,56))).tolist()
        s=build(m);cfg,lut,blocks,_=BS.parse(s);assert len(lut)==2048 and len(blocks)==8
        raw=rng.integers(0,256,8192).tolist();stats={};out=BS.simulate(s,raw,len(raw),stats=stats,wrap_rom=True)
        assert all(a==b and a<64 for a,b in zip(out[::2],out[1::2]))
        tokens=np.array(m['encoder'])[np.array(raw)[::2]];state=0;expected=[]
        # Counter/LUT initial state is zero, then the first lookup primes token0.
        for k in range(1,len(tokens)):
            if mode=='pair':row=tokens[k-1]>>1
            elif mode=='midpoint':row=(tokens[k-1]>>3)*4+int(Q.midquad(np.array(raw[2*k-1],dtype=np.uint8)))
            else:row=(tokens[k-1]>>3)*4+state
            value=(np.array(m['packed_map']) if mode=='tracking' else tab)[row,tokens[k]]
            expected.append(int(value)&63);state=int(value)>>6
        # The first map output determines the initial tracking state; derive it.
        if mode=='tracking':
            state=int(np.array(m['packed_map'])[0,tokens[0]])>>6;expected=[]
            for k in range(1,len(tokens)):
                value=m['packed_map'][(int(tokens[k-1])>>3)*4+state][int(tokens[k])];expected.append(value&63);state=value>>6
        assert out[4::2]==expected[:len(out[4::2])],mode
        assert stats['bundles']==len(out)-1
    print('PASS MMSE fixed-encoder optimality, monotone joint loss, pair/midpoint/tracking source schedules')

if __name__=='__main__':main()
