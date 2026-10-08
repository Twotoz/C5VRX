"""Fit physical DAC6 reconstruction without changing executable state/encoder.

Extends C5VRX's weak_signal_fit filtered-video least-squares method. The target
is true CVBS through a shared positive calibration, not a PLL teacher output.
Floating relaxation followed by DAC quantization is not an optimality proof.
"""
import numpy as np
from scipy import signal as sg
from scipy.sparse.linalg import LinearOperator,lsmr
import overlay_fsm as O
import engine as S
import waveforms as V
import lane_profile as L


def dataset(seed,lane_model=None):
    baseline=S.F.load_reference('OVP56');cases=[]
    for standard,rms,cfo in [('PAL',3,1e6),('NTSC',3,1e6),('PAL',1.5,1e6),
                             ('NTSC',5,1e6),('PAL',3,.5e6),('NTSC',3,1.5e6)]:
        for stressed in (False,True):
            for cnr in (6,30):
                c=V.make_case(standard,seed,cnr,L.scale(rms),short=True,stress=stressed,
                              cfo_hz=cfo,stimulus_seed=seed+100000,lane_model=lane_model)
                for key in ('raw','clean','truth','region'):c[key]=c[key][65536:98304]
                c['calibration']=V.M.clean_calibration(S.decode(c['clean'],baseline),c['truth'],3000)
                cases.append(c)
    return cases


def operator(m,cases,strong_weight=8,regularization=.1,edge_weight=1):
    lut=np.array(m['lut'],np.uint16);p=m['params'];k=1024
    ix=[O.model_indices(c['raw'],m,lut) for c in cases]
    weights=[np.sqrt(strong_weight if c['cnr']>=20 else 1) for c in cases]
    norm=np.sqrt(np.maximum(sum(w*w*np.bincount(a,minlength=k) for a,w in zip(ix,weights)),1))
    windows=[];targets=[];sample_weights=[]
    unit=63/O.H.B.DAC_VOLTS[-1]
    for c,w in zip(cases,weights):
        lag,gain,offset=c['calibration'];n=len(c['raw'])
        lo=max(3000,-lag);hi=min(n-3000,n-lag)
        windows.append((lo+lag,hi+lag))
        edges=(np.abs(np.gradient(c['truth']))>.3)|(c.get('region',np.zeros(n))==2)
        sw=np.sqrt(1+(edge_weight-1)*edges[lo:hi]);sample_weights.append(sw)
        targets.append(w*sw*(c['truth'][lo:hi]-offset)/gain*unit)
    sizes=[b-a for a,b in windows];total=sum(sizes);rootreg=np.sqrt(regularization)
    def forward(z):
        x=z/norm;values=[]
        for a,w,(lo,hi),c,sw in zip(ix,weights,windows,cases,sample_weights):
            y=np.zeros(len(c['raw']));y[2:]=np.repeat(x[a],2)
            values.append(w*sw*O.H.B.D.goggle(y)[lo:hi])
        return np.concatenate(values+[rootreg*z])
    def reverse(y):
        acc=np.zeros(k);offset=0
        for a,w,(lo,hi),size,c,sw in zip(ix,weights,windows,sizes,cases,sample_weights):
            yy=np.zeros(len(c['raw']));yy[lo:hi]=w*sw*y[offset:offset+size];offset+=size
            yy=sg.lfilter(*O.H.B.D.GOG,yy[::-1])[::-1][2:].reshape(-1,2).sum(1)
            acc+=np.bincount(a,weights=yy,minlength=k)
        return acc/norm+rootreg*y[total:total+k]
    op=LinearOperator((total+k,k),matvec=forward,rmatvec=reverse)
    return op,norm,np.concatenate(targets+[np.zeros(k)]),total


def fit(m,cases,strong_weight=8,regularization=.1,iterations=60,edge_weight=1):
    op,norm,target,total=operator(m,cases,strong_weight,regularization,edge_weight)
    base=S.F.LEVELS[np.array(m['lut'])&63]
    residual=target-op.matvec(base*norm);residual[total:]=0
    solution=lsmr(op,residual,atol=2e-5,btol=2e-5,maxiter=iterations)
    values=np.clip(base+solution[0]/norm,0,63)
    return values,dict(strong_weight=strong_weight,regularization=regularization,edge_weight=edge_weight,
                       iterations=int(solution[2]),residual=float(solution[3]),
                       method='filtered CVBS relaxed least squares; bounded DAC projection')


def reconstruct(m,values,blend=1):
    lut=np.array(m['lut'],np.uint16);base=S.F.LEVELS[lut&63]
    codes=S.F.nearest(base+blend*(values-base)).astype(np.uint16)
    result=dict(m,lut=((lut&0xffc0)|codes).tolist())
    O.compile_model(result)
    return result
