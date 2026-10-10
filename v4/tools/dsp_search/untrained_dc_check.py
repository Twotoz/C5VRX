import sys,json,multiprocessing as mp,os
sys.path.insert(0,r'C:\Users\leonb\C5VRX-pair-range\v4\tools\dsp_search'); sys.path.insert(0,r'C:\Users\leonb\C5VRX-pair-range\v4\tools')
import numpy as np
def job(sp):
    import cliff_study as S, goggle_lock as G, untrained_search as U
    seed,cnr,dev,dcm=sp
    c=S.make(seed,cnr,('PAL','NTSC')[seed%2],dev,4.2,'texture',extra=dict(dc=complex(dcm,-dcm*.6)))
    ms={'U85':('lut',U.build(dict(layout='4411',token_bits=5,kp=.85,gamma=0.,out='linear',sigma=0.,range=(-4.,8.))))}
    D=S.demods(); out=[]
    for n in ('U85','PAIR_AF','RANGE32','HC50','UP1'):
        k,m=ms[n] if n in ms else S.lookup(D,n)(c)
        r=G.measure(S.run(k,m,c['raw']),S.run(k,m,c['clean']),c)
        out.append((n,cnr,dev,dcm,min(r['h_ok'],r['peak_h_ok']),r['sinad']))
    return out
if __name__=='__main__':
    specs=[(780000+i*13,cnr,dev,dcm) for i,(cnr,dev,dcm) in enumerate([(c,d,m) for c in (8,14,20) for d in (.69,1.0) for m in (0.,.3,.6)])]
    with mp.get_context('spawn').Pool(7) as p: rows=[r for rr in p.map(job,specs) for r in rr]
    for n in ('U85','UP1','PAIR_AF','RANGE32','HC50'):
        print(n.ljust(8),' '.join('dc%.1f: %.2f/%4.1f'%(m,np.mean([r[4] for r in rows if r[0]==n and r[3]==m]),np.mean([r[5] for r in rows if r[0]==n and r[3]==m])) for m in (0.,.3,.6)))
