#!/usr/bin/env python3
"""C5VRX by Twotoz and contributors: asymmetric two-lookup pair FM research.
Extends vector_pair.py and the existing detector study; host evidence only.
"""
import sys
sys.path.insert(0,'v4/tools/detector_study')
import vector_pair as V
import numpy as np
from pathlib import Path

def quant(nlow,threshold):
 i,q=V.D.cells(np.arange(256));z=i+.5+1j*(q+.5);angle=np.angle(z)%(2*np.pi)
 n=56-nlow;lo=abs(z)<threshold if nlow else np.zeros(256,bool)
 token=nlow+np.floor(angle/(2*np.pi)*n).astype(int)%n
 if nlow:token[lo]=np.floor(angle[lo]/(2*np.pi)*nlow).astype(int)%nlow
 phase=np.r_[(np.arange(nlow)+.5)*256/max(1,nlow),(np.arange(n)+.5)*256/n]
 prevphase=(phase[0::2]+phase[1::2])/2
 return token,phase,prevphase

def train(enc,phase,prev,nlow,mode,margin):
 direct=V.D.wrap(phase[None,:]-prev[:,None]);val=direct.copy()
 sums=np.zeros((28,56));cnt=np.zeros((28,56))
 for seed in (101,102):
  sig,noise,_,_=V.T.make_stream(seed);p=np.angle(sig[0::2])*128/np.pi;e=V.D.wrap(np.diff(p))
  for cnr in (2,4,6,8,10,14):
   raw=V.T.noisy_raw(sig,noise,cnr);t=enc[raw[0::2]]
   np.add.at(sums,(t[:-1]>>1,t[1:]),e);np.add.at(cnt,(t[:-1]>>1,t[1:]),1)
 if mode=='low':
  use=(np.arange(28)[:,None]<nlow//2)|(np.arange(56)[None,:]<nlow)
  val=np.where(use & (cnt>30),sums/np.maximum(cnt,1),val)
 if mode=='corr':
  use=(np.arange(28)[:,None]<nlow//2)|(np.arange(56)[None,:]<nlow)
  err=V.D.wrap(sums/np.maximum(cnt,1)-direct)
  val+=np.where(use & (cnt>30),np.clip(err,-16,16),0)
 val=np.clip(val,V.D.LO_BIN-margin,V.D.HI_BIN+margin)
 return np.clip(np.round((val-(V.D.LO_BIN-16))/(V.D.HI_BIN-V.D.LO_BIN+32)*63),0,63).astype(int)

def decode(raw,enc,tab):
 t=enc[raw[0::2]];dac=tab[t[:-1]>>1,t[1:]]
 return V.D.goggle(np.pad(np.repeat(dac,2),(0,len(raw)-2*len(dac))))
