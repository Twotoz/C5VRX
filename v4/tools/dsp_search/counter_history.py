"""C5VRX causal Bayes-risk decoder with counter-carried phase/frequency history.

Phase32 + previous delta8 reside outside RAM. Delta low-bit arithmetic carries
past frequency quadrant into a scalar LUT address, rather than allocating
coarse phase/frequency grids in its word. ADC encode and scalar risk use one
shared 1024-word LUT16. REQUIRES TX20; RX40 consumes two bytes per span.
"""
import numpy as np
from numba import njit
import hardware as H


def source(m):
 s='''# C5VRX by Twotoz/contributors: causal counter history; REQUIRES TX20.
# Raw RX40/raw32K/TX-only; one shared LUT16, eight slots, two bundles/span.
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
    set 25 A7,
    set 26 A6,
    set 27..31 O3..O7,
    read 16,
    write 8,
    addctiah
risk_{k}:
    set 0..7 L0..L7,
    set 8..15 L8..L15,
    set 16..23 A8..A15,
    set 24..26 L,
    set 27..31 O11..O15,
    ldctia
'''
 return s


@njit(cache=True)
def trace(raw,lut):
 n=len(raw)//2;y=np.zeros(n,np.uint8);idxs=np.zeros(n,np.int64)
 a=0;pos=neg=last=0
 for k in range(n):
  y[k]=last&63;h1=(a>>7)&1;h0=(a>>6)&1
  encidx=int(raw[2*k])|256|(h1<<9)
  operand=(pos&248)|1|(h1<<1)|(h0<<2)
  high=((a>>8)+operand+(((a&255)+int(raw[2*k]))>>8))&255
  idxs[k]=high;last=int(lut[high]);value=int(lut[encidx]);pos=value&255
  a=((neg&248)<<8)|high;neg=(value>>8)&255
 return y,idxs


def observation(idx,offset=80):
 carry=1-(idx&1);base=(idx-carry-1)&255
 context=((base>>1)&1)|(((base>>2)&1)<<1)
 delta=((base-(base&7)-offset+128)&255)-128
 return delta*20e6/256,context


def build(phase=None,mean=None,offset=80):
 if offset%8:raise ValueError('offset must preserve encoded history low bits')
 if phase is None:
  i,q=H.B.D.cells(np.arange(256));phase=np.floor((np.angle(i+.5+1j*(q+.5))%(2*np.pi))*32/(2*np.pi)+.5).astype(int)%32
 phase=np.asarray(phase);lut=np.zeros(1024,np.uint16)
 words=(8*phase+1)|(((-8*phase+offset)&255)<<8)
 lut[256:512]=words;lut[768:1024]=words
 for idx in range(256):
  hz,h=observation(idx,offset);j=(int(round(hz/(20e6/32)))%32)+32*h
  target=hz if mean is None else mean[j]
  code=int(np.rint(np.clip((target*256/20e6-H.B.P.OFFSET)*H.B.P.SCALE,0,63)))
  lut[idx]=code
 return dict(name='COUNTER HISTORY',lut=lut.astype(int).tolist(),phase=phase.astype(int).tolist(),offset=offset,
             input_hz=40000000,output_hz=20000000)


def remap(m,dev,centre):
 lut=np.asarray(m['lut'],np.uint16).copy();code=lut[:256].astype(float)
 hz=(code/H.B.P.SCALE+H.B.P.OFFSET)*20e6/256;nominal=1e6+(hz-centre)/dev
 lut[:256]=np.rint(np.clip((nominal*256/20e6-H.B.P.OFFSET)*H.B.P.SCALE,0,63)).astype(np.uint16)
 return dict(m,lut=lut.astype(int).tolist())


def decode(raw,m):
 y,_=trace(raw,np.asarray(m['lut'],np.uint16))
 return H.B.D.goggle(H.B.DAC_VOLTS[np.repeat(y,2)])


def fit(train,strong_weight=3.,sync_weight=1.):
 count=np.zeros(256);phasor=np.zeros(256,complex)
 for c in train:
  raw=c['raw'][::2];z=c['rx_signal'][::2];w=strong_weight if c['cnr']>=13 else 1.
  u=z/np.maximum(abs(z),1e-12);count+=w*np.bincount(raw,minlength=256)
  phasor+=w*(np.bincount(raw,weights=u.real,minlength=256)+1j*np.bincount(raw,weights=u.imag,minlength=256))
 i,q=H.B.D.cells(np.arange(256));angle=np.where(count>10,np.angle(phasor),np.angle(i+.5+1j*(q+.5)))%(2*np.pi)
 phase=np.floor(angle*32/(2*np.pi)+.5).astype(int)%32;m=build(phase)
 count=np.zeros(128);sums=np.zeros(128)
 for c in train:
  _,idx=trace(c['raw'],np.asarray(m['lut'],np.uint16));z=c['rx_signal'][::2][:len(idx)]
  true=np.r_[0,np.angle(z[1:]/z[:-1])*20e6/(2*np.pi)];true=np.r_[0,true[:-1]]
  base=(idx-(1-(idx&1))-1)&255;ctx=((base>>1)&1)|(((base>>2)&1)<<1)
  delta=((base-(base&7)-80+128)&255)-128;bins=((delta//8)%32)+32*ctx
  weights=np.full(len(true),strong_weight if c['cnr']>=13 else 1.)
  weights*=1+(sync_weight-1)*(true<-.8e6)
  count+=np.bincount(bins[16:],weights=weights[16:],minlength=128)
  sums+=np.bincount(bins[16:],weights=weights[16:]*true[16:],minlength=128)
 prior=np.arange(128)%32;prior=np.where(prior>=16,prior-32,prior)*20e6/32
 mean=(sums+16*prior)/(count+16);m=build(phase,mean)
 m.update(strong_weight=strong_weight,sync_weight=sync_weight)
 return m,dict(count=count.tolist(),mean_hz=mean.tolist())
