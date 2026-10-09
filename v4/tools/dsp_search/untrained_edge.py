import sys,json,glob,itertools,numpy as np
sys.path.insert(0,'.')
import ladder_edge as LE, edge_fsm as F, apex_dac as AD, select_edge as SE
from pathlib import Path
def analytic_vectors():
    v=[]
    for a in range(1024):
        ia=a&15;qa=(a>>4)&15   # pair_bits(4411): address 0..3 = I_A, 4..7 = Q_A
        v.append([(ia-16 if ia>7 else ia)+.5,(qa-16 if qa>7 else qa)+.5])
    return v
if __name__=='__main__':
    opts=json.loads(Path('../range_options.json').read_text())['options']
    ep={k:v for k,v in [o for o in opts if o['label']=='EDGE RANGE LAB'][0]['model']['params'].items() if k not in('fit_deviation','fit_centre_hz')}
    av=analytic_vectors()
    cands={'EDGE pinned (learned tokens)':ep}
    for kp,ki,det,rel in itertools.product((.7,1.),(.1,.15),('tanh','clip'),(False,True)):
        cands[f'untrained kp{kp} ki{ki} {det} rel{int(rel)}']=dict(ep,observation_vectors=av,kp=kp,ki=ki,detector=det,reliability=rel)
    train=[c for c in LE.cases(451000,per=3,afc=True) if c['cnr'] in (2,4,6,8,13,30)]
    def obj(p):
        r=AD.evaluate(train,{'m':lambda c:F.synthesize(dict(p,**c['fit']))})
        return np.mean([v[1] for k in (13,30) for v in r[(k,'m')]])-.5*sum(v[0] for k in (2,4,6) for v in r[(k,'m')])-10*np.mean([v[2] for k in (2,4,6) for v in r[(k,'m')]])
    ranked=sorted(((obj(p),n) for n,p in cands.items() if n.startswith('untrained')),reverse=True)
    best=ranked[0][1];print('best untrained on training seeds:',best)
    test=[c for s in (471000,472000,473000) for c in LE.cases(s,per=4,afc=True)]
    use={'EDGE pinned':cands['EDGE pinned (learned tokens)'],'EDGE untrained':cands[best]}
    rows=AD.evaluate(test,{n:(lambda p:lambda c:F.synthesize(dict(p,**c['fit'])))(p) for n,p in use.items()})
    strong=[np.fromfile(f,np.uint8) for f in sorted((Path.home()/'c5vrx4-static-224006').glob('*.bin'))]
    weak=[np.fromfile(f,np.uint8) for f in sorted((Path.home()/'c5vrx4-static-224103').glob('*.bin'))]
    for n,p in use.items(): print(n,'real clicks %.2f'%SE.real_click_increase(F.synthesize(dict(p,fit_deviation=1.23,fit_centre_hz=1.96e6)),strong,weak))
    for c in LE.CNR: print(c,' | '.join('%s miss %3d SINAD %4.1f false %.2f'%(n,sum(v[0] for v in rows[(c,n)]),np.mean([v[1] for v in rows[(c,n)]]),np.mean([v[2] for v in rows[(c,n)]])) for n in use),flush=True)
