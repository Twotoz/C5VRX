"""C5VRX finite-state regime inference on a full-resolution arithmetic history.

Phase64 resides in counter A/output register; past delta resides in A.low.
A two-state regime PROXY lives in LUT bit6. Its residue interpretation
confounds one-span-older sign history when signs change. Do not interpret
this negative prototype as an exact Bayesian filter or an impossibility bound.
One shared LUT16 contains decoder and risk words, no independent second RAM.
No true C/N/amplitude/video parameters enter the runtime state machine.
"""
import numpy as np
from numba import njit
import hardware as H


def source(m):
 s='''# C5VRX by Twotoz/contributors: counter Bayesian risk; REQUIRES TX20.
# RX40/raw32K, single LUT16 2048 bytes, no raster or CPU sample DSP.
cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 16
lut '''+' '.join(map(str,m['lut']))+'\n'
 for k in range(4):
  s+=f'''decode_{k}:
    set 0..5 L0..L5,
    set 8..15 O8..O15,
    set 16..23 0..7,
    set 24 H,
    set 25 L6,
    set 26..31 O2..O7,
    read 16,
    write 8,
    addctiah
risk_{k}:
    set 0..7 L0..L7,
    set 8..15 L8..L15,
    set 16..23 A8..A15,
    set 24 L,
    set 25 A7,
    set 26..31 O10..O15,
    ldctia
'''
 return s


@njit(cache=True)
def trace(raw,lut):
 n=len(raw)//2;y=np.zeros(n,np.uint8);idxs=np.zeros(n,np.int64)
 a=0;pos=neg=last=0
 for k in range(n):
  y[k]=last&63;regime=(last>>6)&1
  encidx=int(raw[2*k])|256|(regime<<9)
  operand=(pos&252)|1|(regime<<1)
  high=((a>>8)+operand+(((a&255)+int(raw[2*k]))>>8))&255
  hint=(a>>7)&1;idx=high|(hint<<9);idxs[k]=idx
  last=int(lut[idx]);value=int(lut[encidx]);pos=value&255
  a=(((neg&252)|(hint<<1))<<8)|high;neg=(value>>8)&255
 return y,idxs


def observation(index):
 d=index&255;h=index>>9
 carry=1-(d&1);base=(d-carry)&255
 regime=(((base-1)&3)//2)^h
 signed=((base-1-2*regime-2*h+128)&255)-128
 f=signed*20e6/256
 return f,h,regime


def decoder_words():
 i,q=H.B.D.cells(np.arange(256));phase=np.floor((np.angle(i+.5+1j*(q+.5))%(2*np.pi))*64/(2*np.pi)+.5).astype(int)%64
 v=(4*phase+1)|(((-4*phase)&255)<<8)
 lut=np.zeros(1024,np.uint16);lut[256:512]=v;lut[768:1024]=v
 return lut


def physical_code(hz):
 return np.rint(np.clip((hz*256/20e6-H.B.P.OFFSET)*H.B.P.SCALE,0,63)).astype(np.uint16)


def build(stats=None,prior=.9,sync_weight=1.,mode='bayes'):
 lut=decoder_words()
 for idx in list(range(256))+list(range(512,768)):
  f,h,r=observation(idx);bin_=int(round(f/(20e6/64)))%64;j=bin_+64*h
  if stats is None:
   p=float(-3e6<=f<=6e6);target=f
  else:
   prob=np.asarray(stats['likelihood'])[:,j];prior_h=(prior[1] if r else prior[0]) if isinstance(prior,(tuple,list)) else (prior if r else 1-prior)
   p=prob[1]*prior_h/max(prob[1]*prior_h+prob[0]*(1-prior_h),1e-30)
   mu=np.asarray(stats['mean_hz'])[:,j];target=p*mu[1]+(1-p)*mu[0]
   if sync_weight!=1:
    w=np.asarray(stats['sync_mass'])[:,j]*sync_weight+1-np.asarray(stats['sync_mass'])[:,j]
    target=(p*mu[1]*w[1]+(1-p)*mu[0]*w[0])/max(p*w[1]+(1-p)*w[0],1e-30)
   if mode=='strong_anchor' and p>.75:target=f
  lut[idx]=int(physical_code(np.array([target]))[0])|(int(p>=.5)<<6)
 return dict(name='COUNTER BAYES',lut=lut.astype(int).tolist(),prior=prior,sync_weight=sync_weight,mode=mode,
             input_hz=40000000,output_hz=20000000,phase_states=64,confidence_states=2)


def remap(m,dev,centre):
 lut=np.array(m['lut'],np.uint16).copy()
 for idx in list(range(256))+list(range(512,768)):
  code=lut[idx]&63;hz=(code/H.B.P.SCALE+H.B.P.OFFSET)*20e6/256
  out=physical_code(np.array([1e6+(hz-centre)/dev]))[0]
  lut[idx]=(lut[idx]&0xffc0)|int(out)
 return dict(m,lut=lut.astype(int).tolist())


def decode(raw,m):
 y,_=trace(raw,np.asarray(m['lut'],np.uint16))
 return H.B.D.goggle(H.B.DAC_VOLTS[np.repeat(y,2)])


def fit(cases,rounds=3):
 m=build();history=[]
 for iteration in range(rounds):
  counts=np.zeros((2,128));sums=np.zeros_like(counts);sync=np.zeros_like(counts)
  for c in cases:
   _,idxs=trace(c['raw'],np.asarray(m['lut'],np.uint16))
   z=c['rx_signal'][::2][:len(idxs)];true=np.r_[0,np.angle(z[1:]/z[:-1])*20e6/(2*np.pi)]
   # Scalar queried at span k describes interval ending at A[k-1].
   true=np.r_[0,true[:-1]]
   di=idxs&255;hint=idxs>>9;carry=1-(di&1);base=(di-carry)&255
   r=(((base-1)&3)//2)^hint
   signed=((base-1-2*r-2*hint+128)&255)-128
   bins=((signed//4)%64)+64*hint
   regime=int(c['cnr']>=13) # offline latent-regime label, never runtime input
   counts[regime]+=np.bincount(bins[16:],minlength=128)
   sums[regime]+=np.bincount(bins[16:],weights=true[16:],minlength=128)
   sync[regime]+=np.bincount(bins[16:],weights=(true[16:]<-.8e6),minlength=128)
  priorhz=(np.arange(128)%64);priorhz=np.where(priorhz>=32,priorhz-64,priorhz)*20e6/64
  mean=(sums+16*priorhz)/(counts+16)
  likelihood=(counts+16)/(counts+16).sum(1)[:,None]
  stats=dict(likelihood=likelihood.tolist(),mean_hz=mean.tolist(),sync_mass=((sync+1)/(counts+2)).tolist())
  m=build(stats);history.append(dict(iteration=iteration,samples=counts.sum(1).astype(int).tolist()))
 return stats,history
