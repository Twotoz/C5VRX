"""Joint deterministic FSM policy optimization with a straight-through surrogate.

Forward rollout uses ONE hard token and ONE hard state, exactly like hardware.
Softmax derivatives are used only offline for truncated backpropagation.
This nonconvex surrogate is not an exact gradient of a discrete transducer,
not a posterior vector secretly required by runtime, and not an optimality proof.
"""
import numpy as np
from numba import njit
import omega_student as S


def softmax(x,temp):
    y=x/temp;y=y-y.max(axis=-1,keepdims=True)
    e=np.exp(y);return e/e.sum(axis=-1,keepdims=True)


@njit(cache=True)
def gradient(addr,target,states,encoder,transition,values,ep,tp,weight):
    N=len(addr);S_,T=values.shape
    ge=np.zeros_like(ep);gt=np.zeros_like(tp);gc=np.zeros_like(values)
    future=np.zeros(S_);loss=0.
    for n in range(N-1,-1,-1):
        a=addr[n];s=states[n];t=encoder[a]
        error=values[s,t]-target[n];loss+=weight[n]*error*error/N
        dy=2*weight[n]*error/N
        gc[s,t]+=dy
        expected=0.
        for u in range(S_):expected+=tp[s,t,u]*future[u]
        for u in range(S_):gt[s,t,u]+=tp[s,t,u]*(future[u]-expected)
        token_grad=np.empty(T)
        for k in range(T):
            z=dy*values[s,k]
            for u in range(S_):z+=future[u]*tp[s,k,u]
            token_grad[k]=z
        avg=0.
        for k in range(T):avg+=ep[a,k]*token_grad[k]
        for k in range(T):ge[a,k]+=ep[a,k]*(token_grad[k]-avg)
        previous=np.empty(S_)
        for j in range(S_):
            z=dy*values[j,t]
            for u in range(S_):z+=future[u]*tp[j,t,u]
            previous[j]=max(-.5,min(.5,z))
        future=previous
    return loss,ge,gt,gc


@njit(cache=True)
def rollout(addr,encoder,transition,initial):
    states=np.empty(len(addr),np.int64);s=initial
    for k in range(len(addr)):
        states[k]=s;s=transition[s,encoder[addr[k]]]
    return states,s


def optimize(sequences,model,epochs=3,chunk=64,seed=2910190):
    bits=model['params']['token_bits'];T=1<<bits;S_=1024//T
    lut=np.asarray(model['lut'],np.uint16)
    encoder=(lut>>(16-bits)).astype(np.int64)
    transitions=((lut&((1<<(16-bits))-1))>>6).reshape(S_,T).astype(np.int64)
    values=((lut&63).astype(float)/63.).reshape(S_,T)
    el=np.zeros((1024,T));el[np.arange(1024),encoder]=4.
    tl=np.zeros((S_,T,S_));tl[np.arange(S_)[:,None],np.arange(T)[None,:],transitions]=4.
    params=[el,tl,values];mom=[np.zeros_like(p) for p in params];var=[np.zeros_like(p) for p in params]
    rng=np.random.default_rng(seed);tick=0;history=[];best=None
    addresses=[S.addresses(c['raw'],model['params']['pair_layout']) for c in sequences]
    import omega_bayes as B
    targets=[B.codes(c['hz']).astype(float)/63. for c in sequences]
    def snapshot():
        enc=np.argmax(el,axis=1);tr=np.argmax(tl,axis=2)
        codes=np.rint(np.clip(values,0,1)*63).astype(np.uint16)
        words=(codes.ravel()|(tr.ravel().astype(np.uint16)<<6))|(enc.astype(np.uint16)<<(16-bits))
        return dict(model,name='SEQUENCE PARTICLE FSM',lut=words.astype(int).tolist())
    def evaluate():
        enc=np.argmax(el,axis=1);tr=np.argmax(tl,axis=2);err=[]
        for a,y in zip(addresses,targets):
            old,_=rollout(a,enc,tr,0);p=values[old,enc[a]]
            w=1+3*(y<.22);err.append(np.mean(w*(p-y)**2))
        return float(np.mean(err))
    baseline=evaluate();best=(baseline,snapshot());best_epoch=0;history.append(dict(epoch=0,teacher_code_loss=baseline))
    for epoch in range(epochs):
        temp=max(.7,1.4-.25*epoch)
        for i in rng.permutation(len(sequences)):
            a=addresses[i];y=targets[i];state=0
            for start in range(0,len(a)-chunk+1,chunk):
                aa=a[start:start+chunk];yy=y[start:start+chunk]
                ep=softmax(el,temp);tp=softmax(tl,temp)
                enc=np.argmax(el,axis=1);tr=np.argmax(tl,axis=2)
                states,state=rollout(aa,enc,tr,state)
                w=1+3*(yy<.22)
                loss,ge,gt,gc=gradient(aa,yy,states,enc,tr,values,ep,tp,w)
                grads=[ge/temp,gt/temp,gc];tick+=1
                for k,(p,g) in enumerate(zip(params,grads)):
                    np.clip(g,-.2,.2,out=g)
                    mom[k]*=.9;mom[k]+=.1*g;var[k]*=.999;var[k]+=.001*g*g
                    rate=.003 if k==2 else .015
                    p-=rate*(mom[k]/(1-.9**tick))/(np.sqrt(var[k]/(1-.999**tick))+1e-8)
                np.clip(values,0,1,out=values)
        score=evaluate();history.append(dict(epoch=epoch+1,teacher_code_loss=score,temperature=temp))
        print('sequence epoch',epoch+1,'loss',score,flush=True)
        if score<best[0]:best=(score,snapshot());best_epoch=epoch+1
    result=best[1];result['params']=dict(result['params'],sequence_optimization=True,selected_optimization_epoch=best_epoch)
    return result,dict(seed=seed,epochs=epochs,chunk=chunk,history=history,best_loss=best[0],selected_epoch=best_epoch)
