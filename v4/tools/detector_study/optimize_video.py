#!/usr/bin/env python3
"""C5VRX joint demod compiler: filtered-video LUT fit and midpoint context.

Offline convex quadratic table solve (before DAC bounds/rounding), plus local
encoder search. Hard hardware feasibility is compiled separately. No global
optimum or RF-sensitivity claim. Extends optimize_pair/vector56 study.
"""
import argparse,json
from pathlib import Path
import numpy as np
from scipy import signal as sg
from scipy.sparse.linalg import LinearOperator,lsmr
from scipy.optimize import minimize
import optimize_pair as P
D=P.D


def midquad(raw):
    return ((raw&8)>>3)|((raw&128)>>6)


def hist(seeds,profile):
    w=np.zeros(4*65536);s=w.copy();q=w.copy()
    weights={'edge':(1,3,3,2,1,1),'balanced':(1,2,3,3,2,3)}[profile]
    for seed in seeds:
        for cfo,dev,kind,amp in [(1e6,6.7e6,'random',1),(.5e6,6.7e6,'bars',.8),(1.5e6,7.5e6,'multitone',1.2)]:
            sig,noise,target,_=P.stream(seed,cfo=cfo,deviation=dev,kind=kind)
            for cnr,weight in zip(P.CNRS,weights):
                raw=P.raw_at(sig,noise,cnr,amp);t=raw[::2].astype(int)
                ix=midquad(raw[1::2][:-1]).astype(int)*65536+t[:-1]*256+t[1:]
                ix,y=ix[1500:],target[1500:]
                w+=weight*np.bincount(ix,minlength=4*65536)
                s+=weight*np.bincount(ix,weights=y,minlength=4*65536)
                q+=weight*np.bincount(ix,weights=y*y,minlength=4*65536)
    return tuple(a.reshape(4,256,256) for a in (w,s,q))


def fit(enc,h):
    w,s,_=h
    ix=(((enc[:,None]>>3)*4+np.arange(4)[:,None,None])*56+enc[None,:]).ravel()
    cnt=np.bincount(ix,weights=w.ravel(),minlength=28*56).reshape(28,56)
    sums=np.bincount(ix,weights=s.ravel(),minlength=28*56).reshape(28,56)
    # Unseen midpoint contexts fall back to endpoint phase (7 parent classes).
    e=enc;weight=w.sum((0,1))+w.sum((0,2))+1
    i,q=D.cells(np.arange(256));a=np.angle(i+.5+1j*(q+.5))
    def phase(labels,n):
        z=np.bincount(labels,weights=weight*np.cos(a),minlength=n)+1j*np.bincount(labels,weights=weight*np.sin(a),minlength=n)
        return np.angle(z)*128/np.pi
    prev=np.repeat(phase(enc>>3,7),4);curr=phase(enc,56)
    prior=np.clip((D.wrap(curr[None,:]-prev[:,None])-P.OFFSET)*P.SCALE,0,63)
    return np.clip(np.divide(sums,cnt,out=prior.copy(),where=cnt>0),0,63),cnt


def loss(enc,tab,h):
    w,s,q=h
    y=tab[(enc[:,None]>>3)*4+np.arange(4)[:,None,None],enc[None,:]]
    return float(np.sum(w*y*y-2*s*y+q)/w.sum())


def optimize(enc,h,iterations=10):
    enc=enc.copy();w,s,_=h;cs=np.arange(56);history=[]
    for it in range(iterations):
        tab,_=fit(enc,h);before=loss(enc,tab,h);moves=0
        for r in np.argsort(-(w.sum((0,1))+w.sum((0,2)))):
            old=int(enc[r]);cost=np.zeros(56)
            for mid in range(4):
                a=tab[(cs>>3)*4+mid][:,enc];b=tab[(enc>>3)*4+mid][:,cs].T
                cost+=(w[mid,r]*a*a-2*s[mid,r]*a).sum(1)+(w[mid,:,r]*b*b-2*s[mid,:,r]*b).sum(1)
                aa=tab[(cs>>3)*4+mid,old];bb=tab[(old>>3)*4+mid,cs];cc=tab[(cs>>3)*4+mid,cs]
                cost+=w[mid,r,r]*(cc*cc-aa*aa-bb*bb)-2*s[mid,r,r]*(cc-aa-bb)
            c=int(np.argmin(cost))
            if cost[c]+1e-8<cost[old]:enc[r]=c;moves+=1
        tab,_=fit(enc,h);after=loss(enc,tab,h);assert after<=before+1e-7
        history.append(dict(loss=after,moves=moves))
        if not moves:break
    return enc,tab,history


def indices(raw,model):
    e=np.array(model['encoder']);t=e[raw[::2]];n=model['current_tokens']
    if model.get('midpoint'):
        rows=(t[:-1]>>3)*4+midquad(raw[1::2][:-1])
    else:rows=t[:-1]>>model['previous_shift']
    return rows*n+t[1:]


def decode(raw,model):
    ix=indices(raw,model);tab=np.array(model['map']).ravel()
    return D.goggle(np.pad(np.repeat(tab[ix],2),(0,len(raw)-len(ix)*2)))


def train_video(model,profile,regularization,level_weight=0):
    """Exact linear operator includes duplicate DAC bytes and goggle LPF.
    LSMR fits the convex regularized floating table; final DAC bounds/rounding
    are explicit. The encoder is fixed here, so this is not a global solve.
    """
    base=np.array(model['map'],float).ravel();k=len(base);samples=[];counts=np.zeros(k)
    weights={'edge':(1,3,3,2,1,1),'balanced':(1,2,3,3,2,3)}[profile]
    for seed in (421,422):
        for cfo,dev,kind,amp in [(1e6,6.7e6,'random',1),(.5e6,6.7e6,'bars',.8),(1.5e6,7.5e6,'multitone',1.2)]:
            sig,noise,clean,ire=P.stream(seed,n=32768,cfo=cfo,deviation=dev,kind=kind)
            clean_y=D.goggle(np.repeat(clean,2))
            target=D.goggle(((ire-30)*dev/140*P.D.BIN50+cfo*P.D.BIN50-P.OFFSET)*P.SCALE)
            # Choose alignment on CLEAN training signals, never candidate/noisy test output.
            lag=min(range(-12,13),key=lambda l:np.mean((clean_y[3000:-3000]-np.roll(target[:-2],l)[3000:-3000])**2))
            target=np.roll(target[:-2],lag)
            for cnr,weight in zip(P.CNRS,weights):
                raw=P.raw_at(sig,noise,cnr,amp);ix=indices(raw,model)
                samples.append((ix,target,np.sqrt(weight)))
                counts+=weight*np.bincount(ix,minlength=k)
    norm=np.sqrt(np.maximum(counts,1));sizes=[len(t)-6000 for _,t,_ in samples];total=sum(sizes)
    rootreg=np.sqrt(regularization)
    constraints=[];levels=[]
    if level_weight:
        # Preserve the pinned baseline's clean absolute DAC transfer, not a
        # gain/offset-fit metric. No selection/final video is used here.
        baseline=json.loads((P.ROOT/'tools/vlp56_codebook.json').read_text())
        baseline.update(current_tokens=56,previous_shift=1)
        base_table=np.array(baseline['map']).ravel()
        for radius in (1.25,1.5,1.75,2,2.25,2.5,3,3.5,4,4.5,5,5.5):
            for cfo in (.25e6,.5e6,.75e6,1e6,1.25e6,1.5e6,1.75e6,2e6):
                for ire in (-40,-20,0,20,40,60,80,100):
                    t=np.arange(16000)/40e6
                    raw=D.raw_bytes(radius*np.exp(2j*np.pi*(cfo+(ire-30)*6.7e6/140)*t))
                    idx=indices(raw,model)[500:]
                    constraints.append(np.bincount(idx,minlength=k)/len(idx))
                    levels.append(np.mean(base_table[indices(raw,baseline)[500:]]))
    C=np.array(constraints).reshape(-1,k);levels=np.array(levels)
    cw=np.sqrt(max(total,1)*level_weight/max(len(C),1))
    def forward(z):
        x=z/norm;out=[]
        for ix,t,weight in samples:out.append(weight*D.goggle(np.repeat(x[ix],2))[3000:-3000])
        return np.concatenate(out+[rootreg*z,cw*(C@x)])
    def reverse(y):
        acc=np.zeros(k);offset=0
        for (ix,t,weight),size in zip(samples,sizes):
            yy=np.zeros(len(t));yy[3000:-3000]=weight*y[offset:offset+size];offset+=size
            # Adjoint of the finite causal IIR: reverse, filter, reverse.
            yy=sg.lfilter(*D.GOG,yy[::-1])[::-1].reshape(-1,2).sum(1)
            acc+=np.bincount(ix,weights=yy,minlength=k)
        return acc/norm+rootreg*y[total:total+k]+cw*(C.T@y[total+k:])/norm
    op=LinearOperator((total+k+len(C),k),matvec=forward,rmatvec=reverse)
    target=np.concatenate([weight*t[3000:-3000] for _,t,weight in samples]+[np.zeros(k),cw*levels])
    residual=target-forward(base*norm);residual[total:total+k]=0
    sol=lsmr(op,residual,atol=2e-5,btol=2e-5,maxiter=80)
    floating=base+sol[0]/norm
    bound_iterations=0
    if level_weight:
        # Clipping an unconstrained optimum is not a bounded optimum. Solve
        # the actual convex quadratic with DAC bounds before integer rounding.
        lo=-base*norm;hi=(63-base)*norm
        def fg(z):
            error=op.matvec(z)-residual
            return .5*np.dot(error,error),op.rmatvec(error)
        opt=minimize(fg,np.clip(sol[0],lo,hi),jac=True,method='L-BFGS-B',
                     bounds=list(zip(lo,hi)),options=dict(maxiter=400,ftol=1e-11,gtol=1e-5,maxls=30))
        floating=base+opt.x/norm;bound_iterations=int(opt.nit)
    out=dict(model);out['map']=np.rint(np.clip(floating,0,63)).astype(int).reshape(np.array(model['map']).shape).tolist()
    out['fit']=dict(profile=profile,regularization=regularization,lsmr_stop=int(sol[1]),iterations=int(sol[2]),training_seeds=[421,422],clipped_entries=int(np.sum((floating<0)|(floating>63))),level_weight=level_weight,bounded_iterations=bound_iterations,bounded_converged=bool(opt.success) if level_weight else None,max_level_error_dac=float(np.max(abs(C@np.array(out['map']).ravel()-levels))) if len(C) else None)
    return out


def evaluate(models,seeds,full=False):
    rows=[]
    for seed in seeds:
        for cfo,dev,kind,amp,dc,skew in [(1e6,6.7e6,'random',1,0j,1),(.25e6,5.5e6,'bars',.6,.2-.3j,1.1),(2e6,8e6,'multitone',1.5,0j,.9)]:
            sig,noise,_,ire=P.stream(seed,n=131072 if full else 65536,cfo=cfo,deviation=dev,kind=kind);truth=D.goggle(ire)
            for cnr in P.CNRS:
                raw=P.raw_at(sig,noise,cnr,amp,dc,skew)
                for m in models:
                    y=decode(raw,m)
                    if full:D.truth=truth;tot,bands,clicks=D.score(y)
                    else:tot,clicks=P.quick_score(y,truth);bands=[]
                    rows.append(dict(seed=seed,cfo=cfo,deviation=dev,kind=kind,cnr=cnr,model=m['name'],sinad=float(tot),clicks=float(clicks),bands=list(map(float,bands))))
    return rows


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--pair-output',type=Path,required=True);args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    old=json.loads((P.ROOT/'tools/vlp56_codebook.json').read_text());old.update(name='VLP56',current_tokens=56,previous_tokens=28,previous_shift=1,stride=64)
    bases=[old]
    # Only stage-1 selection candidates; its final results do not tune this solve.
    for p in sorted(args.pair_output.glob('joint*.json')):bases.append(json.loads(p.read_text()))
    for profile in ('edge','balanced'):
        h=hist((411,412,413),profile)
        for low in (0,8):
            enc,tab,trace=optimize(P.initialize(56,.5,low),h)
            m=dict(name=f'mid56-{profile}-low{low}',span_ns=50,current_tokens=56,previous_tokens=28,stride=64,midpoint=True,previous_shift=3,encoder=enc.tolist(),map=np.rint(tab).astype(int).tolist(),trace=trace,training_seeds=[411,412,413])
            bases.append(m);print(m['name'],trace[-1],flush=True)
    models=bases.copy()
    for m in bases:
        for reg in (.01,.1):
            v=train_video(m,'balanced',reg);v['name']=m['name']+f'-video{reg}';models.append(v)
            print('VIDEO',v['name'],v['fit'],flush=True)
    selection=evaluate(models,(521,522));sm=P.summary(selection)
    (args.output/'selection.json').write_text(json.dumps(selection,indent=2)+'\n')
    def objective(m):
        r=sm[m['name']];weak=np.mean([r[str(c)][0] for c in (0,2,4,6)])
        # A strong-signal floor avoids winning by crushing useful bandwidth.
        return weak-2*max(0,sm['VLP56']['14'][0]-.8-r['14'][0])
    ranked=sorted(models,key=objective,reverse=True);print('RANK',[(m['name'],round(objective(m),3)) for m in ranked],flush=True)
    for m in ranked:(args.output/(m['name']+'.json')).write_text(json.dumps(m,indent=2)+'\n')
    top=ranked[:3];(args.output/'winner.json').write_text(json.dumps(top[0],indent=2)+'\n')
    print('SELECTION',json.dumps({m['name']:sm[m['name']] for m in top+[old]}),flush=True)
    final=evaluate(top+[old],(721,722,723),full=True)
    (args.output/'final.json').write_text(json.dumps(final,indent=2)+'\n')
    summary=dict(selection=sm,final=P.summary(final),winner=top[0]['name'],seed_policy='411-413 encoder,421-422 filtered tables,521-522 selection,721-723 final')
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print('FINAL',json.dumps(summary['final']),flush=True)

if __name__=='__main__':main()
