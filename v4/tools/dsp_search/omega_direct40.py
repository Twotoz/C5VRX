#!/usr/bin/env python3
"""One-lookup-per-25ns alternative: raw I2/Q2 and 64 learned belief states.

This deliberately bypasses the encoder lookup. More observation time, fewer
IQ bits per observation. Not promoted or timing-proven merely by assembly.
"""
import argparse
import json
from pathlib import Path
import numpy as np
from sklearn.cluster import MiniBatchKMeans
from numba import njit
import omega_bayes as B
import omega_student as S
import ladder_edge as LE
from omega_research import measure, controls, summarize, utility
import hardware as H
import bs_model as BS


def source(m):
    lut=m['lut']; text='# C5VRX by Twotoz/contributors: experimental direct IQ2x2 belief40.\n'
    text+='cfg prefetch true\ncfg eof_on downstream\ncfg trailing_bytes 0\ncfg lut_width_bits 16\nlut '+' '.join(map(str,lut))+'\n'
    for k in range(8):
        text+=f'step{k}:\n    set 0..5 L0..L5,\n'
        for j,bit in enumerate(m['bits']):text+=f'    set {16+j} {bit},\n'
        text+='    set 20..25 L6..L11,\n    read 8,\n    write 8,\n    nop\n'
    return text


def address(raw,bits):
    a=np.zeros(len(raw),np.int64)
    for j,bit in enumerate(bits):a|=((raw>>bit)&1).astype(np.int64)<<j
    return a


@njit(cache=True)
def codes(raw,lut,bits):
    out=np.zeros(len(raw),np.uint8);state=0
    for k in range(len(raw)-1):
        a=0
        for j in range(4):a|=((raw[k]>>bits[j])&1)<<j
        w=lut[state*16+a];out[k+1]=w&63;state=(w>>6)&63
    return out


def decode(c,m):
    from pair_autofit import remap
    mm=remap(m,c['fit']['fit_deviation'],c['fit']['fit_centre_hz'])
    return H.B.D.goggle(H.B.DAC_VOLTS[codes(c['raw'],np.array(mm['lut'],np.uint16),np.array(m['bits']))])


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--teacher-config',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    if a.output.exists():ap.error('fresh directory required')
    a.output.mkdir(parents=True); cfg=B.setup(**json.loads(a.teacher_config.read_text()))
    train=LE.cases(610190,per=2,afc=True);seq=[]
    for c in train:
        hz,x=B.filter(c['raw'],cfg,stride=1);seq.append((c,hz,x))
    xx=np.concatenate([s[2] for s in seq]);hz=np.concatenate([s[1] for s in seq]);scale=np.array([1,1,2,2,.5,.5,.5,.5,.5,.5,.5,2,1,1.])
    xx*=scale;cl=MiniBatchKMeans(n_clusters=64,random_state=610194,n_init=3,batch_size=4096).fit(xx)
    models=[]
    for bits in ((6,7,2,3),(5,7,1,3),(4,7,0,3)):
        addresses=[address(c['raw'],bits) for c,_,_ in seq];ad=np.concatenate(addresses)
        enc=np.arange(16);tr=np.zeros((64,16),np.int64);dac=np.full((64,16),B.codes(np.array([1e6]))[0],np.int64)
        labels=cl.predict(xx);old=[];off=0
        for c,hh,_ in seq:
            ll=labels[off:off+len(hh)];old.append(np.r_[0,ll[:-1]]);off+=len(hh)
        old=np.concatenate(old);history=[]
        for rnd in range(6):
            ix=old*16+ad;count=np.bincount(ix,minlength=1024)
            sums=np.stack([np.bincount(ix,weights=xx[:,j],minlength=1024) for j in range(14)],1)
            avg=sums/np.maximum(count,1)[:,None];next_=cl.predict(avg)
            tr.ravel()[count>0]=next_[count>0]
            mean=np.bincount(ix,weights=hz,minlength=1024)/np.maximum(count,1)
            dac.ravel()[count>0]=B.codes(mean[count>0])
            old=np.concatenate([S.trace(aa,enc,tr) for aa in addresses])
            history.append(dict(round=rnd,visited=int(np.count_nonzero(count))))
        m=dict(name='OMEGA DIRECT40',bits=bits,lut=(dac.ravel()|(tr.ravel()<<6)).tolist())
        metrics=[measure(c,lambda cc:decode(cc,m)) for c in train]
        models.append((utility(metrics),m))
        (a.output/f'bits{bits[0]}.json').write_text(json.dumps(dict(model=m,training=metrics,history=history),indent=1))
        print('direct40',bits,utility(metrics),flush=True)
    _,m=max(models,key=lambda v:v[0]);(a.output/'selected.bsasm').write_text(source(m))
    raw=np.random.default_rng(1940).integers(0,256,70000,dtype=np.uint8)
    actual=BS.simulate(source(m),raw,len(raw),wrap_rom=True)
    np.testing.assert_array_equal(np.array(actual)&63,codes(raw,np.array(m['lut']),np.array(m['bits'])))
    rows=[];fns=controls();fns['DIRECT40']=lambda c:decode(c,m)
    for c in LE.cases(1010190,per=2,afc=True):
        for n,fn in fns.items():rows.append(dict(cnr=c['cnr'],model=n,metrics=measure(c,fn)))
    (a.output/'heldout.json').write_text(json.dumps(dict(summary=summarize(rows),rows=rows,source_equivalence_samples=70000,model=m),indent=1))
    print(json.dumps(summarize(rows),indent=1),flush=True)

if __name__=='__main__':main()
