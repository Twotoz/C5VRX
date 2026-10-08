"""Learn PAIR observation vectors for C5VRX by Twotoz/contributors.

For each 10-bit address of both raw IQ40 samples, estimate the mean unit
phasor of the clean received carrier at sample A (an MMSE-style circular
mean). Training uses only seeded synthetic signals that are never used for
selection or confirmation; the firmware receives only the resulting LUT.
"""
import numpy as np
import overlay_fsm as O
C=O.C
import waveforms as W

MIXES={
    'uniform':(2,4,6,8,10,14,20,30),
    'weak':(0,2,4,4,6,6,8,10,14,30),
    'strong':(4,8,14,20,24,30,30),
}


def addresses(raw,layout):
    word=raw[0::2].astype(np.int64)|(raw[1::2].astype(np.int64)<<8)
    address=np.zeros(len(word),np.int64)
    for j,bit in enumerate(C.pair_bits(layout)):address|=((word>>bit)&1)<<j
    return address


def learn(layout,seed,mix='uniform',cases=32,lane='fine'):
    if mix not in MIXES:raise ValueError('unknown training mixture')
    rng=np.random.default_rng(seed);acc=np.zeros(1024,complex);count=np.zeros(1024)
    for i in range(cases):
        c=W.make_case(('PAL','NTSC')[i%2],seed+i,float(rng.choice(MIXES[mix])),float(rng.uniform(1.5,5)),
                      short=True,cfo_hz=float(rng.uniform(.5e6,1.5e6)),stimulus_seed=seed+1000+i,
                      lane_model=lane,include_traces=True,stress=i%5==4)
        a=addresses(c['raw'],layout);u=c['rx_signal'][0::2][:len(a)]
        u=u/np.maximum(abs(u),1e-12)
        acc+=np.bincount(a,weights=u.real,minlength=1024)+1j*np.bincount(a,weights=u.imag,minlength=1024)
        count+=np.bincount(a,minlength=1024)
    mean=acc/np.maximum(count,1)
    # Require a few observations; rare addresses fall back to geometry.
    mean[count<4]=0
    scale=np.maximum(abs(mean),1);mean/=scale
    vectors=np.round(np.c_[mean.real,mean.imag]*(1-1e-7),6)
    return vectors.tolist(),dict(layout=layout,seed=seed,mix=mix,cases=cases,lane=lane,
                                 occupied=int(np.sum(count>=4)),samples=int(count.sum()))


def test():
    v,info=learn('4411',4242,cases=4)
    a=np.asarray(v);assert a.shape==(1024,2) and np.all(np.hypot(*a.T)<=1)
    assert info['occupied']>200
    print('PASS pair decoder learning',info)


if __name__=='__main__':test()
