#!/usr/bin/env python3
"""Training-seed-only phase codebooks; research sweep, no firmware changes."""
from vector56 import *
# Endpoint codebook built from training-only raw histograms, not video targets.
i,q=V.D.cells(np.arange(256));ang=np.angle(i+.5+1j*(q+.5))%(2*np.pi)
w=np.zeros(256)
for seed in (101,102):
 sig,noise,_,_=V.T.make_stream(seed)
 for cnr in (6,8,10,14):w+=np.bincount(V.T.noisy_raw(sig,noise,cnr),minlength=256)
cs=[]
for shift in np.arange(0,1,.125):
 centres=(np.arange(56)+shift)*2*np.pi/56
 for it in range(30):
  d=np.angle(np.exp(1j*(ang[:,None]-centres[None,:])))
  enc=np.argmin(abs(d),axis=1)
  for k in range(56):
   m=enc==k
   if w[m].sum():centres[k]=np.angle(np.sum(w[m]*np.exp(1j*ang[m])))%(2*np.pi)
  centres=np.sort(centres)
 d=np.angle(np.exp(1j*(ang[:,None]-centres[None,:])));enc=np.argmin(abs(d),axis=1)
 ph=centres*128/np.pi;prev=[]
 for k in range(28):
  m=(enc>>1)==k;prev.append(np.angle(np.sum(w[m]*np.exp(1j*ang[m])))%(2*np.pi)*128/np.pi)
 for margin in (12,16,24):
  tab=train(enc,ph,np.array(prev),0,'plain',margin)
  cs.append((f'kmeans-shift{shift}-margin{margin}',enc,tab))
r={n:[] for n,_,_ in cs};refs={n:[] for n in ('adj40','hc50')}
for seed in (207,208):
 sig,noise,_,ire=V.T.make_stream(seed);V.D.truth=V.D.goggle(ire[::2])
 for cnr in (2,4,6,8,14):
  raw=V.T.noisy_raw(sig,noise,cnr)
  for n,enc,tab in cs:r[n].append(V.score(decode(raw,enc,tab)))
  refs['adj40'].append(V.score(V.D.DESIGNS['adj40'](raw)));refs['hc50'].append(V.score(V.H.DESIGNS['hc50p6'](raw)))
rank=sorted(cs,key=lambda c:np.mean(np.array(r[c[0]]).reshape(2,5,2).mean(0)[:,0]),reverse=True)
for n,a in refs.items():print(n,np.array(a).reshape(2,5,2).mean(0).round(2).tolist(),flush=True)
for n,enc,tab in rank[:8]:print(n,np.array(r[n]).reshape(2,5,2).mean(0).round(2).tolist(),flush=True)
# Research output only; never replace the pinned firmware codebook automatically.
import json
print(json.dumps({'encoder': rank[0][1].tolist(), 'map': rank[0][2].tolist()}))
