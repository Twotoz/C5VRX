"""Typed, bounded FM expression grammar for C5VRX by Twotoz/contributors.

All expression results are radians per 50-ns endpoint span; confidence is
dimensionless. Offline expressions synthesize a single lookup, not a CPU loop.
"""
import copy
import numpy as np

LEAVES=('ovp','phase','conjugate','cross','ratio','polynomial','piecewise','sign','midpoint','history')
OPS=('mix','confidence','huber','saturate','shrink','softsign','predict')


def wrap(a):return (a+np.pi)%(2*np.pi)-np.pi


def validate(e,depth=0,stateful=False):
    if depth>4:raise ValueError('expression depth budget exceeded')
    if e['unit']!='rad/span':raise ValueError('incompatible physical units')
    op=e['op']
    if op in LEAVES:
        if op=='history' and not stateful:raise ValueError('history needs compiled state')
        return
    if op not in OPS:raise ValueError('unknown operation')
    if len(e['args'])!=(2 if op in ('mix','confidence','predict') else 1):raise ValueError('arity')
    if not np.isfinite(e['k']) or not 0<=e['k']<=2:raise ValueError('unbounded coefficient')
    for child in e['args']:validate(child,depth+1,stateful)


def evaluate(e,f):
    op=e['op']
    if op in LEAVES:return f[op]
    x=evaluate(e['args'][0],f);k=e['k']
    if op in ('mix','confidence','predict'):
        y=evaluate(e['args'][1],f)
        weight=np.clip(k if op=='mix' else (1-f['confidence'])**max(k,.05),0,1)
        return x+weight*wrap(y-x)
    if op=='huber':return np.clip(x,-max(k,.05),max(k,.05))
    if op=='saturate':return np.clip(x,-np.pi*np.clip(k,.1,1),np.pi*np.clip(k,.1,1))
    if op=='shrink':return x/(1+k*(1-f['confidence']))
    if op=='softsign':return x/(1+k*abs(x)/np.pi)
    raise ValueError(op)


def generate(rng,stateful=False,depth=0):
    if depth>=3 or rng.random()<.45:
        return dict(op=str(rng.choice(LEAVES if stateful else LEAVES[:-1])),unit='rad/span')
    op=str(rng.choice(OPS))
    n=2 if op in ('mix','confidence','predict') else 1
    return dict(op=op,unit='rad/span',k=float(rng.uniform(0,2)),
                args=[generate(rng,stateful,depth+1) for _ in range(n)])


def mutate(e,rng,stateful=False):
    e=copy.deepcopy(e)
    if e['op'] in LEAVES or rng.random()<.3:return generate(rng,stateful)
    if rng.random()<.5:e['k']=float(np.clip(e['k']+rng.normal(0,.2),0,2))
    else:
        j=int(rng.integers(len(e['args'])));e['args'][j]=mutate(e['args'][j],rng,stateful)
    return e


def recombine(a,b,rng):
    return dict(op='mix',unit='rad/span',k=float(rng.random()),args=[copy.deepcopy(a),copy.deepcopy(b)])


def features(model):
    from hardware import B
    enc=np.array(model['encoder']);n=model['current_tokens'];shift=model['previous_shift']
    i,q=B.D.cells(np.arange(256));z=i+.5+1j*(q+.5)
    def centre(labels,count):
        w=np.bincount(labels,minlength=count)
        return (np.bincount(labels,weights=z.real,minlength=count)+
                1j*np.bincount(labels,weights=z.imag,minlength=count))/np.maximum(w,1)
    c=centre(enc,n)[None,:];prev=centre(enc>>shift,n>>shift)[:,None]
    context=4 if model.get('tracking') else 1<<len(model['middle_bits'])
    p=np.repeat(prev,context,axis=0);prod=c*p.conjugate()
    phase=wrap(np.angle(c)-np.angle(p));conj=np.angle(prod)
    magnitude=abs(prod)+1e-12;x=np.clip(prod.imag/magnitude,-1,1)
    ratio=np.arctan2(prod.imag,abs(prod.real)+.25)
    poly=x+x**3/6+3*x**5/40
    pwl=np.where(abs(x)<.7,1.1*x,np.sign(x)*(.77+2.1*(abs(x)-.7)))
    sign=np.sign(prod.imag)*np.arccos(np.clip(prod.real/magnitude,-1,1))
    confidence=magnitude/(magnitude+4)
    midpoint=phase.copy()
    bits=model['middle_bits']
    if bits==[3,7]:
        quad=np.arange(len(p))%4
        mid=(np.where(quad&2,-1,1)+1j*np.where(quad&1,-1,1))[:,None]
        midpoint=wrap(np.angle(mid*p.conjugate()))+wrap(np.angle(c*mid.conjugate()))
    history=np.zeros_like(phase)
    if model.get('tracking'):
        history[:]=np.array(model['history_centres'])[np.arange(len(p))%4,None]
    import weak_signal_fit as F
    reference=F.load_reference('OVP56');re=np.array(reference['encoder']);rt=np.array(reference['map'])
    # Compile an OVP-distilled prior into this model's one transition lookup.
    # It does not add a hidden runtime teacher, table or lookup.
    labels=((enc[:,None]>>shift)*n+enc[None,:]).ravel()
    values=rt[(re[:,None]>>1),re[None,:]].ravel()
    count=np.bincount(labels,minlength=(n>>shift)*n)
    prior=np.divide(np.bincount(labels,weights=values,minlength=len(count)),count,
                    out=np.full(len(count),32.),where=count>0).reshape(n>>shift,n)
    ovp=np.repeat((prior/B.P.SCALE+B.P.OFFSET)*np.pi/128,context,axis=0)
    return dict(ovp=ovp,phase=phase,conjugate=conj,cross=np.arcsin(x),ratio=ratio,
                polynomial=poly,piecewise=pwl,sign=sign,midpoint=midpoint,
                confidence=confidence,history=history)
