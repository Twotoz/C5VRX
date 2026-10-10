"""Bayesian policy test of the previously unsuccessful contextual schedule.

The old contextual phase-bin architecture is not a new invention. This test
changes its inference policy to likelihood-space observation compression and
closed-loop belief projection. It discards sample B explicitly. Two lookups,
one shared 2-KiB LUT16, eight slots, DAC6 duplicated at TX40 are unchanged.
"""
import numpy as np
import belief_rom as B
from omega_bayes import component_likelihood,codes
import compile_overlay as C


def setup():
    cfg=B.setup(phases=64,frequencies=33)
    P=cfg['phases'];F=len(cfg['freq'])
    angle=2*np.pi*np.arange(P)/P
    em=[]
    for amp,sigma in cfg['regimes']:
        pi=component_likelihood(amp*np.cos(angle),sigma)
        pq=component_likelihood(amp*np.sin(angle),sigma)
        em.append(np.broadcast_to((pi[:,:,None]*pq[:,None,:]).reshape(P,256),(F,P,256)).reshape(F*P,256))
    cfg['emission']=np.concatenate(em).astype(np.float32)
    return cfg


def snapshots(raw,cfg):
    em=B.likelihood(cfg['emission']);G=len(em)
    b=np.full(G,1/G,np.float32);snap=[]
    for n,q in enumerate(raw[::2]):
        b=B.prediction(b,cfg)[0]*em[:,q];b/=b.sum()
        if n<32 or n%32==31:snap.append(b.copy())
    return np.asarray(snap)


def fit(snap,cfg,bits,seed=3210190):
    from sklearn.cluster import MiniBatchKMeans
    states=1024//(1<<bits);T=1<<bits;N=states//4
    P=cfg['phases'];R=len(cfg['regimes']);F=len(cfg['freq'])
    angle=2*np.pi*np.arange(P)/P
    prior=B.prediction(snap,cfg).reshape(-1,R,F,P).sum((1,2))
    phasor=prior@np.exp(1j*angle)
    group=(np.floor(np.angle(phasor)*2/np.pi).astype(int)%4)
    diffuse=np.full((1,len(cfg['emission'])),1/len(cfg['emission']),np.float32)
    boot=B.likelihood(cfg['emission']).T*diffuse
    boot/=boot.sum(1,keepdims=True)
    pp=B.prediction(boot,cfg).reshape(-1,R,F,P).sum((1,2))
    bootgroup=np.floor(np.angle(pp@np.exp(1j*angle))*2/np.pi).astype(int)%4
    book=[]
    for g in range(4):
        data=snap[group==g]
        # All four phase sectors are required, not inferred from test cases.
        if len(data)<N:raise ValueError('training lacks sector coverage')
        acquisition=B.codebook(boot[bootgroup==g],5,seed+g)[1:]
        count=N-5 if g==0 else N-4
        bb=np.vstack([acquisition,B.codebook(data,count+1,seed+g)[1:]])
        if g==0:bb=np.vstack([diffuse,bb])
        book.append(bb)
    book=np.concatenate(book).astype(np.float32)
    predicted=B.prediction(book,cfg)
    enc=np.empty((4,256),np.int64)
    for g in range(4):
        prior=predicted[g*N:(g+1)*N].mean(0)
        posterior=cfg['emission'].T*prior[None,:]
        mass=posterior.sum(1);posterior/=np.maximum(mass[:,None],1e-30)
        enc[g]=MiniBatchKMeans(n_clusters=T,random_state=seed+g,n_init=3,
                    batch_size=256,max_iter=40).fit_predict(np.sqrt(posterior),sample_weight=mass)
    nxt=np.empty((states,T),np.int64);dac=np.empty((states,T),np.uint8)
    freq=np.tile(np.repeat(cfg['freq'],P),R)
    similarity_all=[]
    for g in range(4):
        em=np.stack([cfg['emission'][:,enc[g]==t].sum(1) for t in range(T)],1)
        post=(predicted[g*N:(g+1)*N,None,:]*B.likelihood(em).T[None,:,:]).reshape(N*T,-1)
        post/=post.sum(1,keepdims=True)
        similarity=np.sqrt(post)@np.sqrt(book).T
        nxt[g*N:(g+1)*N]=similarity.argmax(1).reshape(N,T)
        dac[g*N:(g+1)*N]=codes(post@freq*1e6).reshape(N,T)
        similarity_all.append(similarity.max(1))
    lut=dac.ravel().astype(np.uint16)|(nxt.ravel().astype(np.uint16)<<6)|(enc.ravel().astype(np.uint16)<<(16-bits))
    m=dict(name='CONTEXT BAYES ROM',params=dict(token_bits=bits,context_bits=2,
           context_state_bits=True,belief_fsm=True,phases=4,confidence_groups=1,
           training_seed=seed,generator='belief_context_rom'),lut=lut.astype(int).tolist())
    C.build(m)
    return m,dict(mean_hellinger_distortion=float(np.mean(1-np.concatenate(similarity_all))),
                  states=states,tokens=T,encoder_contexts=4,raw_sample_B_used=False)
