#!/usr/bin/env python3
"""Hardware-constrained joint pair-FM search for C5VRX by Twotoz/contributors.

Extends vector56/designs/designs2. Fixed-encoder DAC means are exact empirical
MMSE solutions (rounded to DAC6); joint Lloyd search is local, not a proof of
 global optimality. Search cannot silently overwrite firmware. NumPy/SciPy are
 offline-only dependencies. Training, selection and final seeds are disjoint.
"""
import argparse
import json
from pathlib import Path
import numpy as np
from scipy import signal as sg
import vector56 as V56
V = V56.V
D = V.D
ROOT = Path(__file__).resolve().parents[2]
CNRS = (0, 2, 4, 6, 8, 14)
SCALE = 63 / (D.HI_BIN - D.LO_BIN + 32)
OFFSET = D.LO_BIN - 16


def stream(seed, n=65536, cfo=1e6, deviation=6.7e6, kind='random'):
    rng = np.random.default_rng(seed)
    t = np.arange(n)/80e6
    if kind == 'random':
        x = sg.lfilter(sg.firwin(801, 4.5e6, fs=80e6), 1, rng.normal(size=n))
        ire = np.clip(50+30*x/max(x.std(), 1e-9), 0, 100)
    elif kind == 'multitone':
        ire = 50+sum(7*np.sin(2*np.pi*f*t+rng.uniform(0, 6.28))
                     for f in (0.3e6, 1e6, 2e6, 3.58e6, 4.43e6, 4.8e6))
    elif kind == 'bars':
        ire = (np.floor(t*0.4e6) % 5)*25
        # PAL burst-frequency detail in the active waveform, not full PAL timing.
        ire = np.clip(ire+8*np.sin(2*np.pi*4.43e6*t), 0, 100)
    else:
        raise ValueError(kind)
    ire = np.where((t % 64e-6)<4.7e-6, -40, ire)
    ire = sg.lfilter(sg.firwin(201, 6e6, fs=80e6), 1, ire)
    freq = cfo+(ire-30)*deviation/140
    carrier = np.exp(2j*np.pi*np.cumsum(freq)/80e6)
    sig = D.chan(carrier)[::2]; sig /= np.sqrt(np.mean(abs(sig)**2))
    noise = D.chan((rng.normal(size=n)+1j*rng.normal(size=n))/np.sqrt(2))[::2]
    noise /= np.sqrt(np.mean(abs(noise)**2))
    # Actual clean endpoint frequency; source/filter distortion is scored against IRE.
    phase = np.angle(sig[::2])*128/np.pi
    target = (D.wrap(np.diff(phase))-OFFSET)*SCALE
    return sig, noise, target, ire[::2]


def raw_at(sig, noise, cnr, amplitude=1, dc=0j, skew=1):
    z = (sig*np.sqrt(10**(cnr/10))+noise)*D.SIG_CELL*np.sqrt(2)*amplitude
    z = z.real*skew+1j*z.imag+dc
    return D.raw_bytes(z).astype(np.uint8)


def histogram(seeds, profile):
    w = np.zeros(65536); s = w.copy(); q = w.copy()
    scenarios = [(1e6,6.7e6,'random',1),(.5e6,6.7e6,'bars',.8),
                 (1.5e6,7.5e6,'multitone',1.2)]
    cnrs = (0,2,4,6,8,14)
    weights = {'edge':(1,3,3,2,1,1),'balanced':(1,2,3,3,2,3)}[profile]
    for seed in seeds:
        for cfo,dev,kind,amp in scenarios:
            sig,noise,target,_ = stream(seed,cfo=cfo,deviation=dev,kind=kind)
            for cnr,weight in zip(cnrs,weights):
                r=raw_at(sig,noise,cnr,amp)[::2]
                ix=r[:-1].astype(int)*256+r[1:]
                # Discard source/filter start-up from training as from scoring.
                ix,y=ix[1500:],target[1500:]
                w+=weight*np.bincount(ix,minlength=65536)
                s+=weight*np.bincount(ix,weights=y,minlength=65536)
                q+=weight*np.bincount(ix,weights=y*y,minlength=65536)
    return tuple(a.reshape(256,256) for a in (w,s,q))


def layout(n):
    # n=32 retains all five token bits; n=40/48/56 retain token>>1.
    return (32,32,0) if n==32 else (n//2,64,1)


def fallback(enc,n,weight):
    rows,_,shift=layout(n)
    i,q=D.cells(np.arange(256));angle=np.angle(i+.5+1j*(q+.5))
    def phases(labels,count):
        re=np.bincount(labels,weights=weight*np.cos(angle),minlength=count)
        im=np.bincount(labels,weights=weight*np.sin(angle),minlength=count)
        return np.angle(re+1j*im)*128/np.pi
    p=phases(enc,n);prev=phases(enc>>shift,rows)
    return np.clip((D.wrap(p[None,:]-prev[:,None])-OFFSET)*SCALE,0,63)


def fit(enc,n,h,prior_strength=0):
    w,s,_=h;rows,_,shift=layout(n)
    ix=((enc[:,None]>>shift)*n+enc[None,:]).ravel()
    cw=np.bincount(ix,weights=w.ravel(),minlength=rows*n).reshape(rows,n)
    cs=np.bincount(ix,weights=s.ravel(),minlength=rows*n).reshape(rows,n)
    prior=fallback(enc,n,w.sum(0)+w.sum(1)+1)
    out=np.divide(cs+prior_strength*prior,cw+prior_strength,
                  out=prior.copy(),where=(cw+prior_strength)>0)
    return np.clip(out,0,63),cw


def empirical_loss(enc,n,tab,h):
    w,s,q=h;shift=layout(n)[2];p=tab[(enc[:,None]>>shift),enc[None,:]]
    return float(np.sum(w*p*p-2*s*p+q)/w.sum())


def optimize(enc,n,h,iterations=12):
    """Sequential reassignment lowers fixed-table loss; refitting cannot raise it.
    Both appearances of raw cell r, including the (r,r) intersection, count.
    No order/phase assumption is imposed on learned encoder labels.
    """
    enc=enc.copy();w,s,_=h;shift=layout(n)[2];choices=np.arange(n)
    history=[]
    for it in range(iterations):
        tab,_=fit(enc,n,h);before=empirical_loss(enc,n,tab,h);moves=0
        for r in np.argsort(-(w.sum(0)+w.sum(1))):
            old=int(enc[r]);a=tab[choices>>shift][:,enc];b=tab[enc>>shift][:,choices].T
            costs=(a*a*w[r]-2*a*s[r]).sum(1)+(b*b*w[:,r]-2*b*s[:,r]).sum(1)
            # The two sums assumed the other side stayed old at (r,r).
            aa=tab[choices>>shift,old];bb=tab[old>>shift,choices];cc=tab[choices>>shift,choices]
            costs+=w[r,r]*(cc*cc-aa*aa-bb*bb)-2*s[r,r]*(cc-aa-bb)
            c=int(np.argmin(costs))
            if costs[c]+1e-8 < costs[old]:enc[r]=c;moves+=1
        tab,_=fit(enc,n,h);after=empirical_loss(enc,n,tab,h)
        assert after<=before+1e-7,(before,after)
        history.append({'loss':after,'moves':moves})
        if not moves:break
    return enc,tab,history


def initialize(n,shift=0,low=0):
    i,q=D.cells(np.arange(256));z=i+.5+1j*(q+.5);a=np.angle(z)%(2*np.pi)
    enc=low+(np.floor(a/(2*np.pi)*(n-low)+shift).astype(int)%(n-low))
    if low:
        m=abs(z)<1.8;enc[m]=np.floor(a[m]/(2*np.pi)*low).astype(int)%low
    return enc


def decode(raw,enc,tab,n):
    t=enc[raw[::2]];shift=layout(n)[2]
    dac=tab[t[:-1]>>shift,t[1:]]
    return D.goggle(np.pad(np.repeat(dac,2),(0,len(raw)-2*len(dac))))


def quick_score(y,truth):
    sl=slice(3000,-3000);best=(float('inf'),0)
    for lag in range(-5,7):
        x=np.roll(y,-lag)[sl];t=truth[sl]
        var=np.var(x);a=np.mean((x-x.mean())*(t-t.mean()))/max(var,1e-12)
        err=a*(x-x.mean())-(t-t.mean());mse=np.mean(err*err)
        if mse<best[0]:best=(mse,float(1000*np.mean(abs(err)>40)))
    return [float(10*np.log10(max(np.var(truth[sl]),1e-9)/max(best[0],1e-9))),best[1]]


def evaluate(models,seeds,stress=False,full=False):
    rows=[]
    scenarios=[(1e6,6.7e6,'random',1,0j,1)]
    if stress:scenarios += [(.25e6,5.5e6,'bars',.6,.2-.3j,1.1),
                           (2e6,8e6,'multitone',1.5,0j,.9)]
    for seed in seeds:
        for cfo,dev,kind,amp,dc,skew in scenarios:
            sig,noise,_,ire=stream(seed,n=131072 if full else 65536,cfo=cfo,deviation=dev,kind=kind)
            truth=D.goggle(ire)
            for cnr in CNRS:
                raw=raw_at(sig,noise,cnr,amp,dc,skew)
                for name,enc,tab,n in models:
                    y=decode(raw,enc,tab,n)
                    if full:D.truth=truth;tot,bands,clicks=D.score(y);score=[float(tot),float(clicks)];bands=list(map(float,bands))
                    else:score=quick_score(y,truth);bands=[]
                    rows.append(dict(seed=seed,cfo=cfo,deviation=dev,kind=kind,amplitude=amp,cnr=cnr,model=name,sinad=score[0],clicks=score[1],bands=bands))
    return rows


def summary(rows):
    return {name:{str(c):[float(np.mean([r[k] for r in rows if r['model']==name and r['cnr']==c])) for k in ('sinad','clicks')] for c in CNRS} for name in sorted({r['model'] for r in rows})}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    original=json.loads((ROOT/'tools/vlp56_codebook.json').read_text())
    models=[('VLP56',np.array(original['encoder']),np.array(original['map']),56)]
    traces={}
    for profile in ('edge','balanced'):
        h=histogram((401,402,403),profile)
        for n in (32,40,48,56):
            for low in (0,8):
                name=f'joint{n}-{profile}-low{low}'
                init=initialize(n,.5,low)
                if n==56 and low==0:init=np.array(original['encoder'])
                enc,tab,trace=optimize(init,n,h)
                traces[name]=trace
                # Bounded integer deployment, not the unconstrained floating optimum.
                models.append((name,enc,np.rint(tab).astype(int),n))
                print(name,'loss',round(trace[-1]['loss'],4),'steps',len(trace),flush=True)
    selection=evaluate(models,(501,502),stress=True)
    (args.output/'selection.json').write_text(json.dumps(selection,indent=2)+'\n')
    sm=summary(selection);print('SELECTION',json.dumps(sm),flush=True)
    def objective(m):
        name=m[0];r=sm[name]
        return np.mean([r[str(c)][0] for c in (0,2,4,6)])
    ranked=sorted(models,key=objective,reverse=True)
    top=ranked[:3]
    print('TOP',[m[0] for m in top],flush=True)
    # Freeze candidates before looking at FINAL data; no reselection on final seeds.
    for name,enc,tab,n in top:
        (args.output/(name+'.json')).write_text(json.dumps(dict(name=name,span_ns=50,current_tokens=n,previous_tokens=layout(n)[0],stride=layout(n)[1],previous_shift=layout(n)[2],encoder=enc.tolist(),map=tab.tolist(),training_seeds=[401,402,403],selection_seeds=[501,502],trace=traces.get(name,[])),indent=2)+'\n')
    (args.output/'winner.json').write_text((args.output/(top[0][0]+'.json')).read_text())
    final=evaluate(top+[models[0]],(601,602,603),stress=True,full=True)
    (args.output/'final.json').write_text(json.dumps(final,indent=2)+'\n')
    print('FINAL',json.dumps(summary(final)),flush=True)
    (args.output/'summary.json').write_text(json.dumps(dict(selection=sm,final=summary(final),winner=top[0][0],seed_policy='401-403 training; 501-502 selection; 601-603 final, never reselection'),indent=2)+'\n')

if __name__=='__main__':main()
