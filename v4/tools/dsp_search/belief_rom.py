"""Causal Bayesian belief projection into the real C5 shared LUT16.

Each ROM state represents a full phase/frequency/amplitude/noise distribution.
Transitions apply prediction and a quantized-observation likelihood to THAT
distribution, rather than regressing the full teacher's trajectory labels.
Hellinger projection closes the recursion. This is an offline ROM generator;
the deployed transducer has one integer state, one token, and one DAC code.
The grid, process prior and Gaussian lane model are approximations, not a
claim of an optimal decoder or an exact model of the undocumented board ADC.
"""
import numpy as np
from numba import njit
from omega_bayes import component_likelihood, codes
from omega_student import addresses
import compile_overlay as C


def setup(layout='4411', phases=64, frequencies=33, jump=.025):
    freq=np.linspace(-5.,7.,frequencies)  # MHz, fixed prior, not supplied VTX
    regimes=np.array([(a,s) for a in (1.5,3.,7.,10.) for s in (.1,1.5,4.)])
    phi=2*np.pi*np.arange(phases)/phases
    # State is the phase at sample B. Sample A precedes B by 25 ns.
    angle=np.broadcast_to(phi,(len(freq),phases))
    angles=(angle-2*np.pi*freq[:,None]/40.,angle)
    words=np.zeros(1024,np.int64)
    for j,bit in enumerate(C.pair_bits(layout)):words|=((np.arange(1024)>>j)&1)<<bit
    em=np.ones((len(regimes),len(freq),phases,1024))
    for r,(amp,sigma) in enumerate(regimes):
        for k,(keep,base) in enumerate(zip(map(int,layout),(4,0,12,8))):
            a=angles[k//2]
            mean=amp*(np.cos(a) if k%2==0 else np.sin(a))
            prob=component_likelihood(mean.ravel(),sigma).reshape(len(freq),phases,16)
            group=np.arange(16)>>(4-keep)
            grouped=np.stack([prob[:,:,group==g].sum(2) for g in range(1<<keep)],2)
            observed=((words>>base)&15)>>(4-keep)
            em[r]*=grouped[:,:,observed]
    em=em.reshape(-1,1024).astype(np.float32)
    assert np.max(abs(em.sum(1)-1))<2e-6
    shift=freq*phases/20.
    return dict(layout=layout,phases=phases,freq=freq,regimes=regimes,
                emission=em,lower=np.floor(shift).astype(np.int64),
                fraction=shift-np.floor(shift),jump=jump)


@njit(cache=True)
def predict(beliefs,R,F,P,lower,fraction,jump):
    result=np.empty_like(beliefs)
    for s in range(len(beliefs)):
        b=beliefs[s].reshape(R,F,P)
        tmp=np.zeros((R,F,P),np.float32)
        # A finite random walk plus contamination in frequency admits fast
        # edges; no raster phase, sync label, or C/N enters this transition.
        for r in range(R):
            for p in range(P):
                total=0.
                for f in range(F):total+=b[r,f,p]
                for f in range(F):
                    tmp[r,f,p]+=jump*total/F
                    for d in range(-2,3):
                        dest=f+d
                        if dest<0:dest=-dest
                        if dest>=F:dest=2*F-2-dest
                        w=(.054,.244,.404,.244,.054)[d+2]
                        tmp[r,dest,p]+=(1-jump)*w*b[r,f,p]
        dest=result[s].reshape(R,F,P)
        for r in range(R):
            for f in range(F):
                for p in range(P):
                    a=(p-lower[f])%P
                    v=(1-fraction[f])*tmp[r,f,a]+fraction[f]*tmp[r,f,(a-1)%P]
                    other=0.
                    for rr in range(R):
                        other+=(1-fraction[f])*tmp[rr,f,a]+fraction[f]*tmp[rr,f,(a-1)%P]
                    dest[r,f,p]=.998*v+.002*other/R
        result[s]/=result[s].sum()
    return result


def prediction(b,cfg):
    return predict(np.ascontiguousarray(np.atleast_2d(b),dtype=np.float32),len(cfg['regimes']),len(cfg['freq']),
                   cfg['phases'],cfg['lower'],cfg['fraction'],cfg['jump'])


def likelihood(em):
    # Emission normalization is checked before generalized-Bayes tempering.
    return ((1-.005)*em+.005/em.shape[1])**.65


def reference(raw,cfg,encoder=None,stride=128):
    """Causal reduced-observation reference, optionally with a token encoder.

    Saved beliefs are posterior snapshots for offline codebook construction.
    Output k sees exactly raw[:2*k+2]. Full address reference isolates the
    10-bit input projection from subsequent token and state compression.
    """
    a=addresses(raw,cfg['layout']);em=cfg['emission']
    if encoder is not None:
        em=np.stack([em[:,encoder==t].sum(1) for t in range(int(encoder.max())+1)],1)
        a=encoder[a]
    em=likelihood(em)
    freq=np.tile(np.repeat(cfg['freq'],cfg['phases']),len(cfg['regimes']))
    b=np.full(em.shape[0],1/em.shape[0],np.float32)
    hz=np.empty(len(a));snap=[]
    for n,t in enumerate(a):
        b=prediction(b,cfg)[0]*em[:,t];b/=b.sum()
        hz[n]=b@freq*1e6
        if n%stride==stride-1:snap.append(b.copy())
    return hz,np.asarray(snap)


def encoder(cfg,bits,seed):
    """Task-independent likelihood-space observation clustering.

    Weighted Hellinger Lloyd clustering of latent posteriors conditional on
    each legal raw address, not geometric phase bins or known clean phasors.
    """
    from sklearn.cluster import MiniBatchKMeans
    em=cfg['emission'].T.copy();mass=em.sum(1)
    x=np.sqrt(em/np.maximum(mass[:,None],1e-30))
    return MiniBatchKMeans(n_clusters=1<<bits,random_state=seed,n_init=3,
                          batch_size=1024,max_iter=30).fit_predict(x,sample_weight=mass)


def predictive_basis(cfg):
    """Next raw-IQ distribution plus task frequency-bin probabilities.

    Prediction is a distribution over future observations, not future IQ.
    This identifies beliefs with similar observable/task consequences rather
    than spending finite states on latent differences invisible to the DAC.
    """
    F=len(cfg['freq']);P=cfg['phases'];R=len(cfg['regimes'])
    angle=2*np.pi*np.arange(P)[None,:]/P+2*np.pi*cfg['freq'][:,None]/40.
    obs=[]
    for amp,sigma in cfg['regimes']:
        pi=component_likelihood((amp*np.cos(angle)).ravel(),sigma)
        pq=component_likelihood((amp*np.sin(angle)).ravel(),sigma)
        obs.append((pi[:,:,None]*pq[:,None,:]).reshape(F*P,256))
    observation=np.concatenate(obs).astype(np.float32)
    task=np.tile(np.repeat(np.eye(F,dtype=np.float32),P,axis=0),(R,1))
    return observation,task


def predictive_features(b,basis):
    observation,task=basis
    # Two equally weighted proper distributions; no arbitrary phase/freq
    # units or test C/N weight. Hellinger geometry bounds both distortions.
    return np.concatenate([np.sqrt(np.maximum(b@observation,0)),
                           np.sqrt(np.maximum(b@task,0))],1)/np.sqrt(2.)


def codebook(snap,states,seed,basis=None):
    from sklearn.cluster import MiniBatchKMeans
    x=np.sqrt(snap) if basis is None else predictive_features(snap,basis)
    km=MiniBatchKMeans(n_clusters=states-1,random_state=seed,n_init=3,
                      batch_size=512,max_iter=30).fit(x)
    if basis is None:
        b=km.cluster_centers_**2;b/=b.sum(1,keepdims=True)
    else:
        labels=km.predict(x);b=np.zeros((states-1,snap.shape[1]),np.float32)
        np.add.at(b,labels,snap)
        # Empty clusters retain a sampled valid belief, never zero density.
        empty=b.sum(1)==0;b[empty]=snap[np.flatnonzero(empty)%len(snap)]
        b/=b.sum(1,keepdims=True)
    # State zero remains a diffuse startup prior. Other beliefs are full
    # distributions; amplitude/noise uncertainty is never a supplied selector.
    return np.vstack([np.full((1,snap.shape[1]),1/snap.shape[1]),b]).astype(np.float32)


def acquisition_codebook(snap,cfg,enc,bits,seed,basis=None):
    """Reserve genuine first-observation posteriors for cold acquisition.

    A lone diffuse prototype among steady-state beliefs is an absorbing
    projection trap: its one-step posterior can remain closer to that prior
    than to every sharp steady-state prototype. Explicit bootstrap posteriors
    fix this representational error without a raster or signal-quality input.
    """
    from sklearn.cluster import MiniBatchKMeans
    T=1<<bits;S=1024//T;N=min(T,S//4)
    em=np.stack([cfg['emission'][:,enc==t].sum(1) for t in range(T)],1)
    prior=np.full((1,em.shape[0]),1/em.shape[0],np.float32)
    boot=prediction(prior,cfg)*likelihood(em).T
    boot/=boot.sum(1,keepdims=True)
    if N<T:
        km=MiniBatchKMeans(n_clusters=N,random_state=seed,n_init=3,
                           batch_size=T).fit(np.sqrt(boot))
        boot=km.cluster_centers_**2;boot/=boot.sum(1,keepdims=True)
    # Remaining prototypes still use the chosen steady-state geometry.
    steady=codebook(snap,S-N,seed,basis)[1:]
    return np.vstack([prior,boot,steady]).astype(np.float32)


def project(cfg,b,enc,bits,basis=None):
    T=1<<bits;S=len(b)
    em=np.stack([cfg['emission'][:,enc==t].sum(1) for t in range(T)],1)
    em=likelihood(em)
    post=(prediction(b,cfg)[:,None,:]*em.T[None,:,:]).reshape(S*T,-1)
    post/=post.sum(1,keepdims=True)
    if basis is None:similarity=np.sqrt(post)@np.sqrt(b).T
    else:similarity=predictive_features(post,basis)@predictive_features(b,basis).T
    nxt=similarity.argmax(1)
    freq=np.tile(np.repeat(cfg['freq'],cfg['phases']),len(cfg['regimes']))
    dac=codes(post@freq*1e6)
    lut=dac.astype(np.uint16)|(nxt.astype(np.uint16)<<6)|(enc.astype(np.uint16)<<(16-bits))
    m=dict(name='BAYES BELIEF ROM',params=dict(token_bits=bits,pair_layout=cfg['layout'],
           belief_fsm=True,phases=4,confidence_groups=1),lut=lut.astype(int).tolist())
    C.build(m)
    return m,dict(mean_hellinger_distortion=float(np.mean(1-similarity.max(1))),
                  distinct_next_states=int(len(np.unique(nxt))),posterior=post)


def decode_reference(raw,cfg,dev=1.,centre=1e6,enc=None):
    import hardware as H
    hz,_=reference(raw,cfg,enc,stride=len(raw)+1)
    out=codes(1e6+(hz-centre)/dev)
    y=np.zeros(len(raw));y[2:]=np.repeat(H.B.DAC_VOLTS[out],2)[:len(raw)-2]
    return H.B.D.goggle(y)
