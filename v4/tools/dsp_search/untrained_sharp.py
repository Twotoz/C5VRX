import sys,json,itertools,numpy as np
sys.path.insert(0,'.')
import ladder_edge as LE, edge_fsm as F, apex_dac as AD, select_edge as SE
from untrained_edge import analytic_vectors
from pair_autofit import remap
from pathlib import Path
if __name__=='__main__':
    opts=json.loads(Path('../range_options.json').read_text())['options']
    ep={k:v for k,v in [o for o in opts if o['label']=='EDGE RANGE LAB'][0]['model']['params'].items() if k not in('fit_deviation','fit_centre_hz')}
    pair=[o for o in opts if o['label']=='PAIR RANGE LAB'][0]['model']
    av=analytic_vectors();cands={}
    for (P,Fq,T),kp,ki,out in itertools.product(((16,2,32),(32,2,16),(16,4,16),(8,4,32)),(.7,1.),(.05,.2),('advance','avg','freq')):
        cands[f'P{P}F{Fq}T{T} kp{kp} ki{ki} {out}']=dict(ep,observation_vectors=av,phases=P,frequencies=Fq,token_bits=int(T).bit_length()-1,kp=kp,ki=ki,output=out,detector='clip',reliability=False,mix=0.)
    train=[c for c in LE.cases(451000,per=3,afc=True) if c['cnr'] in (8,13,20,30)]
    def obj(p):
        r=AD.evaluate(train,{'m':lambda c:F.synthesize(dict(p,**c['fit']))})
        return np.mean([v[1] for k in (13,20,30) for v in r[(k,'m')]])-sum(v[0] for k in (8,13) for v in r[(k,'m')])-10*np.mean([v[2] for k in (8,13,20,30) for v in r[(k,'m')]])
    ranked=sorted(((obj(p),n) for n,p in cands.items()),reverse=True)[:3]
    print('top untrained sharp on training:',[(round(s,1),n) for s,n in ranked])
    best=cands[ranked[0][1]]
    test=[c for s in (471000,472000) for c in LE.cases(s,per=4,afc=True) if c['cnr']>=8]
    rows=AD.evaluate(test,{'PAIR+AF':lambda c:remap(pair,c['fit']['fit_deviation'],c['fit']['fit_centre_hz']),'SHARP untrained':lambda c:F.synthesize(dict(best,**c['fit']))})
    strong=[np.fromfile(f,np.uint8) for f in sorted((Path.home()/'c5vrx4-static-224006').glob('*.bin'))]
    weak=[np.fromfile(f,np.uint8) for f in sorted((Path.home()/'c5vrx4-static-224103').glob('*.bin'))]
    print('real clicks PAIR+AF %.2f  SHARP untrained %.2f'%(SE.real_click_increase(remap(pair,1.23,1.96e6),strong,weak),SE.real_click_increase(F.synthesize(dict(best,fit_deviation=1.23,fit_centre_hz=1.96e6)),strong,weak)))
    for c in (8,10,13,16,20,30): print(c,' | '.join('%s miss %d SINAD %.1f false %.2f'%(n,sum(v[0] for v in rows[(c,n)]),np.mean([v[1] for v in rows[(c,n)]]),np.mean([v[2] for v in rows[(c,n)]])) for n in ('PAIR+AF','SHARP untrained')),flush=True)
