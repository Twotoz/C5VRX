#!/usr/bin/env python3
"""C5VRX offline closed-loop state4/previous-phase7/current56 LUT search.

State is two bits in the LUT word, carried to the next bundle; six DAC bits
remain unchanged. Exactly two LUT8 lookups/50 ns, eight instruction slots.
Teacher state is used only to initialize training; subsequent fits and all
selection/final evaluation use the generated state, never clean/oracle state.
"""
import argparse,ctypes,json,subprocess,tempfile
from pathlib import Path
import numpy as np
import optimize_pair as P
import optimize_video as Q
D=P.D

C_SOURCE=r'''
#include <stdint.h>
void rollout(const uint8_t *raw,int count,const uint8_t *enc,const uint8_t *tab,uint8_t *out,uint8_t *contexts){
 unsigned prev=enc[raw[0]]>>3,state=tab[enc[raw[0]]]>>6;
 for(int k=1;k<count;k++){
  unsigned cur=enc[raw[2*k]],ix=((prev*4+state)*56+cur);
  contexts[k-1]=state;out[k-1]=tab[ix]&63;state=tab[ix]>>6;prev=cur>>3;
 }
}
'''


def kernel():
    scratch=Path(tempfile.mkdtemp(prefix='c5vrx-track-'));(scratch/'roll.c').write_text(C_SOURCE)
    subprocess.run(['cc','-O3','-shared','-fPIC',str(scratch/'roll.c'),'-o',str(scratch/'roll.so')],check=True)
    lib=ctypes.CDLL(str(scratch/'roll.so'));f=lib.rollout
    a=np.ctypeslib.ndpointer(dtype=np.uint8,flags='C_CONTIGUOUS')
    f.argtypes=[a,ctypes.c_int,a,a,a,a];f.restype=None
    return f

RUN=kernel()


def rollout(raw,model):
    raw=np.ascontiguousarray(raw,dtype=np.uint8);enc=np.array(model['encoder'],dtype=np.uint8);table=np.array(model['packed_map'],dtype=np.uint8).ravel()
    count=len(raw)//2;out=np.zeros(count-1,dtype=np.uint8);ctx=out.copy();RUN(raw,count,enc,table,out,ctx)
    return out,ctx


def pack(tab,cuts):
    dac=np.rint(np.clip(tab,0,63)).astype(int)
    state=np.searchsorted(cuts,tab)
    return dac+64*state


def train(enc,cuts,profile):
    sequences=[];weights={'edge':(1,3,3,2,1,1),'balanced':(1,2,3,3,2,3)}[profile]
    for seed in (431,432,433):
        for cfo,dev,kind,amp in [(1e6,6.7e6,'random',1),(.5e6,6.7e6,'bars',.8),(1.5e6,7.5e6,'multitone',1.2)]:
            sig,noise,y,_=P.stream(seed,n=32768,cfo=cfo,deviation=dev,kind=kind)
            for cnr,weight in zip(P.CNRS,weights):sequences.append((P.raw_at(sig,noise,cnr,amp),y,weight))
    model=dict(encoder=enc.tolist());history=[]
    for it in range(10):
        w=np.zeros(4*65536);s=w.copy();q=w.copy()
        for raw,y,weight in sequences:
            t=raw[::2].astype(int)
            if it==0:ctx=np.r_[2,np.searchsorted(cuts,y[:-1])].astype(int)
            else:_,ctx=rollout(raw,model)
            ix=ctx.astype(int)*65536+t[:-1]*256+t[1:];ix,yy=ix[1500:],y[1500:]
            w+=weight*np.bincount(ix,minlength=4*65536);s+=weight*np.bincount(ix,weights=yy,minlength=4*65536);q+=weight*np.bincount(ix,weights=yy*yy,minlength=4*65536)
        h=tuple(x.reshape(4,256,256) for x in (w,s,q))
        if it in (0,3,6):enc,tab,trace=Q.optimize(enc,h,iterations=4)
        else:tab,_=Q.fit(enc,h)
        model.update(encoder=enc.tolist(),packed_map=pack(tab,cuts).tolist(),map=np.rint(tab).astype(int).tolist())
        history.append(float(Q.loss(enc,tab,h)))
    model.update(history=history,span_ns=50,current_tokens=56,previous_tokens=28,stride=64,previous_shift=3,tracking=True,state_cuts=list(cuts),training_seeds=[431,432,433],teacher_initialization_only=True)
    return model


def decode(raw,m):
    out,_=rollout(raw,m)
    return D.goggle(np.pad(np.repeat(out.astype(float),2),(0,2)))


def evaluate(models,seeds,full=False):
    rows=[]
    for seed in seeds:
        for cfo,dev,kind,amp,dc,skew in [(1e6,6.7e6,'random',1,0j,1),(.25e6,5.5e6,'bars',.6,.2-.3j,1.1),(2e6,8e6,'multitone',1.5,0j,.9)]:
            sig,noise,_,ire=P.stream(seed,n=131072 if full else 65536,cfo=cfo,deviation=dev,kind=kind);truth=D.goggle(ire)
            for cnr in P.CNRS:
                raw=P.raw_at(sig,noise,cnr,amp,dc,skew)
                for m in models:
                    y=decode(raw,m) if m.get('tracking') else Q.decode(raw,m)
                    if full:D.truth=truth;tot,bands,clicks=D.score(y)
                    else:tot,clicks=P.quick_score(y,truth);bands=[]
                    rows.append(dict(seed=seed,cfo=cfo,deviation=dev,kind=kind,cnr=cnr,model=m['name'],sinad=float(tot),clicks=float(clicks),bands=list(map(float,bands))))
    return rows


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--video-output',type=Path,required=True);args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    old=json.loads((P.ROOT/'tools/vlp56_codebook.json').read_text());old.update(name='VLP56',current_tokens=56,previous_tokens=28,stride=64,previous_shift=1)
    models=[old,json.loads((args.video_output/'winner.json').read_text())]
    for profile in ('edge','balanced'):
        for cuts in ((16,32,48),(24,32,40),(20,30,40)):
            m=train(np.array(old['encoder']),cuts,profile);m['name']=f'track56-{profile}-'+ '-'.join(map(str,cuts));models.append(m)
            print('TRAIN',m['name'],m['history'],flush=True)
    selection=evaluate(models,(541,542));sm=P.summary(selection)
    def objective(m):
        r=sm[m['name']];weak=np.mean([r[str(c)][0] for c in (0,2,4,6)])
        return weak-2*max(0,sm['VLP56']['14'][0]-.8-r['14'][0])
    rank=sorted(models,key=objective,reverse=True);print('RANK',[(m['name'],round(objective(m),4)) for m in rank],flush=True)
    for m in rank:(args.output/(m['name']+'.json')).write_text(json.dumps(m,indent=2)+'\n')
    (args.output/'winner.json').write_text(json.dumps(rank[0],indent=2)+'\n')
    (args.output/'selection.json').write_text(json.dumps(selection,indent=2)+'\n')
    final=evaluate(rank[:3]+[old],(741,742,743),full=True)
    (args.output/'final.json').write_text(json.dumps(final,indent=2)+'\n')
    summary=dict(selection=sm,final=P.summary(final),winner=rank[0]['name'],seed_policy='431-433 training,541-542 selection,741-743 final, closed-loop state only')
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('FINAL',json.dumps(summary['final']),flush=True)

if __name__=='__main__':main()
