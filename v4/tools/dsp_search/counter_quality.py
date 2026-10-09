"""C5VRX quantized observation-risk table with real endpoint quality evidence.

One extra scalar address bit carries the current endpoint's quantization
uncertainty. No phase bits are taken from the 32-sector arithmetic state.
A previous endpoint-quality bit enters the counter arithmetic itself. Its
ambiguity with the frequency-history bits is marginalized by empirical
conditional Bayes risk, never falsely decoded as an exact regime state.
"""
import numpy as np
from numba import njit
import counter_history as H


def source(m):
 return H.source(m).replace('causal counter history','counter endpoint risk').replace('    set 24..26 L,','    set 24 L,\n    set 25 O9,\n    set 26 L,')


@njit(cache=True)
def trace(raw,lut):
 n=len(raw)//2;y=np.zeros(n,np.uint8);idxs=np.zeros(n,np.int64)
 a=0;pos=neg=last=0
 for k in range(n):
  y[k]=last&63;h1=(a>>7)&1;h0=(a>>6)&1
  encidx=int(raw[2*k])|256|(h1<<9)
  operand=(pos&248)|1|(h1<<1)|(h0<<2)
  high=((a>>8)+operand+(((a&255)+int(raw[2*k]))>>8))&255
  q=(neg>>1)&1;idx=high|(q<<9);idxs[k]=idx;last=int(lut[idx])
  value=int(lut[encidx]);pos=value&255;a=((neg&250)<<8)|high;neg=(value>>8)&255
 return y,idxs


def build(phase,threshold=2.5,mean=None):
 m=H.build(phase);lut=np.asarray(m['lut'],np.uint16)
 i,q=H.H.B.D.cells(np.arange(256));quality=np.hypot(i+.5,q+.5)>=threshold
 for bank in (1,3):lut[bank*256:(bank+1)*256]|=quality.astype(np.uint16)<<9
 for bank in (0,2):
  for d in range(256):
   if mean is not None:target=mean[d+256*(bank//2)]
   else:
    base=(d-(1-(d&1))-1)&255;delta=((base-(base&7)-80+128)&255)-128;target=delta*20e6/256
   lut[d+bank*256]=int(np.rint(np.clip((target*256/20e6-H.H.B.P.OFFSET)*H.H.B.P.SCALE,0,63)))
 return dict(name='COUNTER ENDPOINT RISK',lut=lut.astype(int).tolist(),threshold=threshold,
             input_hz=40000000,output_hz=20000000,phase_states=32)


def remap(m,dev,centre):
 lut=np.asarray(m['lut'],np.uint16).copy()
 for bank in (0,2):
  code=lut[bank*256:(bank+1)*256].astype(float);hz=(code/H.H.B.P.SCALE+H.H.B.P.OFFSET)*20e6/256
  target=1e6+(hz-centre)/dev
  lut[bank*256:(bank+1)*256]=np.rint(np.clip((target*256/20e6-H.H.B.P.OFFSET)*H.H.B.P.SCALE,0,63)).astype(np.uint16)
 return dict(m,lut=lut.astype(int).tolist())


def decode(raw,m):
 y,_=trace(raw,np.asarray(m['lut'],np.uint16));return H.H.B.D.goggle(H.H.B.DAC_VOLTS[np.repeat(y,2)])


def fit(train,threshold,strong_weight):
 phase_model,_=H.fit(train,strong_weight,4.);m=build(phase_model['phase'],threshold)
 count=np.zeros(1024);sums=np.zeros(1024)
 for c in train:
  _,idx=trace(c['raw'],np.asarray(m['lut'],np.uint16));z=c['rx_signal'][::2][:len(idx)]
  true=np.r_[0,np.angle(z[1:]/z[:-1])*20e6/(2*np.pi)];true=np.r_[0,true[:-1]]
  weight=np.full(len(true),strong_weight if c['cnr']>=13 else 1.);weight*=1+3*(true<-.8e6)
  count+=np.bincount(idx[16:],weights=weight[16:],minlength=1024);sums+=np.bincount(idx[16:],weights=weight[16:]*true[16:],minlength=1024)
 mean=np.zeros(512)
 for bank in (0,2):
  for d in range(256):
   base=(d-(1-(d&1))-1)&255;delta=((base-(base&7)-80+128)&255)-128;prior=delta*20e6/256
   idx=d+bank*256;mean[d+256*(bank//2)]=(sums[idx]+16*prior)/(count[idx]+16)
 m=build(phase_model['phase'],threshold,mean);m['strong_weight']=strong_weight
 return m,dict(count=count.tolist(),mean_hz=mean.tolist())
