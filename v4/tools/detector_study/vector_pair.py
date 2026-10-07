#!/usr/bin/env python3
"""C5VRX by Twotoz and contributors: two-bundle vector-pair LUT study.
Research only, no live firmware changes. Builds on designs.py and hw50.py.
8-bit LUT: raw IQ -> 5-bit vector token; token pair -> 6-bit DAC.
"""
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import designs as D
import designs2 as T
import hw50 as H
import bs_model as BS
D.N=1<<16


def quantizer(nlow, threshold, shift=0):
    i,q=D.cells(np.arange(256));z=i+.5+1j*(q+.5)
    ang=np.angle(z)%(2*np.pi);r=np.abs(z)
    nhi=32-nlow
    low=r<threshold if nlow else np.zeros(256,bool)
    phasebin=np.floor(ang/(2*np.pi)*nhi+.5+shift).astype(int)%nhi
    token=phasebin+nlow
    if nlow:
        token[low]=np.floor(ang[low]/(2*np.pi)*nlow+.5+shift).astype(int)%nlow
    phase=np.r_[np.arange(nlow)*256/max(1,nlow),np.arange(nhi)*256/nhi]-shift*256/nhi
    # Low class has its own bin spacing.
    if nlow: phase[:nlow]=np.arange(nlow)*256/nlow-shift*256/nlow
    return token,phase


def decode(raw,enc,table):
    t=enc[raw[0::2]]
    dac=table[t[:-1],t[1:]]
    return D.goggle(np.pad(np.repeat(dac,2),(0,len(raw)-2*len(dac))))


def baseline_map(ph,margin):
    e=D.wrap(ph[None,:]-ph[:,None])
    if margin is not None: e=np.clip(e,D.LO_BIN-margin,D.HI_BIN+margin)
    return np.clip(np.round((e-(D.LO_BIN-16))/(D.HI_BIN-D.LO_BIN+32)*63),0,63).astype(int)


def train(enc,ph,nlow,mode,margin,cnrs=(2,4,6,8,10,14)):
    sums=np.zeros((32,32));cnt=np.zeros((32,32))
    for seed in (101,102):
        sig,noise,_,_=T.make_stream(seed)
        p=np.angle(sig[0::2])*128/np.pi
        e=D.wrap(np.diff(p))
        for cnr in cnrs:
            raw=T.noisy_raw(sig,noise,cnr);tok=enc[raw[0::2]]
            np.add.at(sums,(tok[:-1],tok[1:]),e)
            np.add.at(cnt,(tok[:-1],tok[1:]),1)
    direct=D.wrap(ph[None,:]-ph[:,None]);est=sums/np.maximum(cnt,1)
    if mode=='low':
        use=(np.arange(32)[:,None]<nlow)|(np.arange(32)[None,:]<nlow)
    elif mode=='out':use=(direct<D.LO_BIN-margin)|(direct>D.HI_BIN+margin)
    else:use=np.ones((32,32),bool)
    v=np.where(use & (cnt>30),est,direct)
    if margin is not None:v=np.clip(v,D.LO_BIN-margin,D.HI_BIN+margin)
    return np.clip(np.round((v-(D.LO_BIN-16))/(D.HI_BIN-D.LO_BIN+32)*63),0,63).astype(int)


def program(enc,tab):
    lut=np.zeros(2048,int);lut[:256]=enc;lut[1024:]=tab.reshape(-1,order='F')
    s='cfg prefetch true\ncfg eof_on downstream\ncfg trailing_bytes 0\ncfg lut_width_bits 8\nlut '+' '.join(map(str,lut))+'\n'
    for n in range(4):
        s+=f'''controller_{n}:
    set 0..5 L0..L5,
    set 8..13 L0..L5,
    set 16..23 0..7,
    read 16,
    write 16,
    nop
worker_{n}:
    set 16..20 B5..B9,
    set 21..25 L0..L4,
    set 26 H,
    ldctib
'''
    return s


def prove(enc,tab):
    source=program(enc,tab);rng=np.random.default_rng(912);raw=rng.integers(0,256,4000).tolist()
    stats={};out=BS.simulate(source,raw,4000,stats=stats,wrap_rom=True)
    tok=enc[np.array(raw)[::2]];expected=tab[tok[:-1],tok[1:]]
    observed=np.array(out).reshape(-1,2)
    assert np.array_equal(observed[2:2000,0],expected[:1998])
    assert np.array_equal(observed[:,0],observed[:,1])
    assert stats['bundles']<=4000
    # Exhaustive token pairs in a stream, using an encoder representative per token.
    reps={int(t):i for i,t in enumerate(enc)}
    stream=[]
    for a in reps:
        for b in reps:stream.extend((reps[a],0,reps[b],0))
    stats={};o=np.array(BS.simulate(source,stream,len(stream),stats=stats,wrap_rom=True)).reshape(-1,2)
    t=enc[np.array(stream)[::2]];ex=tab[t[:-1],t[1:]]
    assert np.array_equal(o[2:,0],ex[:len(o)-2])
    assert BS.parse(source)[0]['lut_width_bits']=='8'
    print('PASS: source-driven 8-slot model, 2048-byte LUT, 2 bundles/output pair, duplicate DAC, reachable token pairs',len(reps)**2,flush=True)
    return source


def score(y):
    # Same truth/error criterion as designs.py; lighter lag search for the sweep.
    sl=slice(3000,-3000);best=None
    for lag in range(-4,6):
        yy=np.roll(y,-lag);a,c=np.polyfit(yy[sl],D.truth[sl],1);e=(a*yy+c-D.truth)[sl]
        mse=np.var(e)
        if best is None or mse<best[0]:best=(mse,1000*np.mean(abs(e)>40))
    return 10*np.log10(np.var(D.truth[sl])/best[0]),best[1]


def main():
    candidates=[]
    for nlow,th in ((0,0),(4,1.0),(8,1.0),(8,1.8),(16,1.8)):
        enc,ph=quantizer(nlow,th)
        for mode in ('low','all','out'):
            if nlow==0 and mode=='low':continue
            for margin in (8,16):
                tab=train(enc,ph,nlow,mode,margin)
                candidates.append((f'vp{nlow}-r{th}-{mode}-m{margin}',enc,tab))
    res={n:[] for n,_,_ in candidates};refs={n:[] for n in ('adj40','adj40+clamp','hc50','span50')}
    for seed in (207,208):
        sig,noise,_,ire=T.make_stream(seed);D.truth=D.goggle(ire[::2])
        for cnr in (2,4,6,8,14):
            raw=T.noisy_raw(sig,noise,cnr)
            for name,enc,tab in candidates:res[name].append(score(decode(raw,enc,tab)))
            for name in refs:
                fn={'hc50':H.DESIGNS['hc50p6'],'span50':H.DESIGNS['hw50']}.get(name,D.DESIGNS.get(name))
                refs[name].append(score(fn(raw)))
    print('rows: 2,4,6,8,14 dB C/N; mean SINAD dB / clicks per 1000 over two held-out seeds',flush=True)
    def avg(r):return np.mean(np.array(r).reshape(2,5,2),axis=0)
    for n,r in refs.items():print(n,avg(r).round(2).tolist(),flush=True)
    ranked=sorted(candidates,key=lambda c:np.mean(avg(res[c[0]])[:3,0]),reverse=True)
    for n,enc,tab in ranked[:8]:print(n,avg(res[n]).round(2).tolist(),flush=True)
    n,enc,tab=ranked[0];source=prove(enc,tab)
    Path(__file__).with_name('vector_pair_candidate.bsasm').write_text(source)
    print('BEST',n,flush=True)

if __name__=='__main__':main()
