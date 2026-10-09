"""Causal quantization-aware continuous-phase FM particle reference.

Offline reference, NOT a C5 implementation. Runtime receives only IQ bytes.
Amplitude/noise are persistent inferred hypotheses, not supplied C/N or RMS.
The exact lane-cell likelihood assumes independent Gaussian I/Q noise;
filtered physical noise, imbalance and unknown ADC calibration are limitations.
Power-tempering is generalized Bayes, not an exact posterior for that likelihood.
No raster prior, injected sync, future observations or external C/N selector.
"""
import numpy as np
from numba import njit
from omega_bayes import component_likelihood, codes


def setup(particles=2048, phases=256, acceleration=True, jump=.06,
          innovation=.08, temper=.65, contamination=.005, modes=False):
    # A and sigma independently cover gain changes and noise; no fixed total RMS.
    amps=np.array([1.,2.,3.,4.8,7.,10.])
    sigmas=np.array([.04,.2,.6,1.5,3.,6.])
    regimes=np.array([(a,s) for a in amps for s in sigmas])
    angle=2*np.pi*np.arange(phases)/phases
    likelihood=np.empty((len(regimes),phases,256))
    for r,(a,s) in enumerate(regimes):
        pi=component_likelihood(a*np.cos(angle),s)
        pq=component_likelihood(a*np.sin(angle),s)
        likelihood[r]=(pi[:,:,None]*pq[:,None,:]).reshape(phases,256)
    likelihood=((1-contamination)*likelihood+contamination/256)**temper
    return dict(particles=particles,phases=phases,acceleration=acceleration,
                jump=jump,innovation=innovation,temper=temper,
                contamination=contamination,modes=modes,regimes=regimes,likelihood=likelihood)


@njit(cache=True)
def _filter(raw, likelihood, regimes, count, acceleration, jump, innovation, modes, seed):
    np.random.seed(seed)
    R,P,_=likelihood.shape
    phase=np.random.uniform(0,2*np.pi,count)
    freq=np.random.uniform(-7.,9.,count)
    slope=np.zeros(count)
    motion=np.arange(count)%2
    cls=np.arange(count)%R
    weight=np.full(count,1./count)
    output=np.zeros(len(raw)//2)
    features=np.zeros((len(raw)//2,18))
    ancestors=np.empty(count,np.int64)
    # Fixed-time prediction and observation; all n outputs use only raw[:n+1].
    for n in range(len(raw)):
        total=0.;square=0.
        for j in range(count):
            if modes:
                if np.random.random() < (.006 if motion[j]==0 else .02):
                    motion[j]=1-motion[j]
            if modes and motion[j]==0:
                slope[j]*=.1
                freq[j]+=slope[j]+np.random.normal()*.04
            elif np.random.random()<jump:
                freq[j]+=np.random.normal()*1.8
                slope[j]=0.
            elif acceleration:
                slope[j]=.94*slope[j]+np.random.normal()*innovation
                freq[j]+=slope[j]
            else:
                freq[j]+=np.random.normal()*innovation
            freq[j]=max(-10.,min(10.,freq[j]))
            phase[j]=(phase[j]+2*np.pi*freq[j]/40.)%(2*np.pi)
            # Persistent parameter hypotheses can migrate; injection is prior only.
            if np.random.random()<.001:
                cls[j]=np.random.randint(R)
            x=phase[j]*P/(2*np.pi);p=int(x)%P;d=x-int(x)
            prob=(1-d)*likelihood[cls[j],p,raw[n]]+d*likelihood[cls[j],(p+1)%P,raw[n]]
            weight[j]*=prob;total+=weight[j]
        if total<=1e-300:
            weight[:]=1./count
        else:
            weight/=total
        for j in range(count):square+=weight[j]*weight[j]
        if n%2==1:
            k=n//2;mean=0.;second=0.
            for j in range(count):
                w=weight[j];mean+=w*freq[j];second+=w*freq[j]**2
                features[k,0]+=w*np.cos(phase[j]);features[k,1]+=w*np.sin(phase[j])
                features[k,2]+=w*regimes[cls[j],0];features[k,3]+=w*regimes[cls[j],1]
                features[k,4]+=w*slope[j]
                features[k,10]+=w*freq[j]*np.cos(phase[j])
                features[k,11]+=w*freq[j]*np.sin(phase[j])
                for hidx in range(3):
                    horizon=(1,2,4)[hidx]
                    future=phase[j]+2*np.pi*(horizon*freq[j]+.5*horizon*(horizon+1)*slope[j])/40.
                    features[k,12+2*hidx]+=w*np.cos(future)
                    features[k,13+2*hidx]+=w*np.sin(future)
                features[k,6+min(3,max(0,int((freq[j]+10)/5)))]+=w
            output[k]=mean*1e6
            features[k,5]=max(0.,second-mean**2)**.5
        # Systematic resampling after emitting the posterior estimate.
        if square>2./count:
            u=np.random.random()/count;cumulative=weight[0];i=0
            for j in range(count):
                threshold=u+j/count
                while cumulative<threshold and i<count-1:
                    i+=1;cumulative+=weight[i]
                ancestors[j]=i
            phase=phase[ancestors].copy();freq=freq[ancestors].copy()
            slope=slope[ancestors].copy();cls=cls[ancestors].copy()
            motion=motion[ancestors].copy()
            weight[:]=1./count
    return output,features


def filter(raw,cfg,seed=2300190):
    return _filter(np.asarray(raw,np.uint8),cfg['likelihood'],cfg['regimes'],
                   cfg['particles'],cfg['acceleration'],cfg['jump'],cfg['innovation'],cfg['modes'],seed)


def decode(raw,cfg,dev=1.,centre=1e6,seed=2300190):
    import hardware as H
    hz,x=filter(raw,cfg,seed)
    c=codes(1e6+(hz-centre)/dev)
    y=np.zeros(len(raw));y[2:]=np.repeat(H.B.DAC_VOLTS[c],2)[:len(raw)-2]
    return H.B.D.goggle(y),x,hz
