import sys,json,glob,numpy as np
sys.path.insert(0,'.')
import ladder_edge as LE, edge_fsm as F, apex_dac as AD, engine as S, select_edge as SE
from pathlib import Path
if __name__=='__main__':
    opts=json.loads(Path('../range_options.json').read_text())['options']
    ep={k:v for k,v in [o for o in opts if o['label']=='EDGE RANGE LAB'][0]['model']['params'].items() if k not in('fit_deviation','fit_centre_hz')}
    learned=np.load(r'C:/Users/leonb/C5VRX-pair-range/v4/docs/data/apex/cvt_dac_values.npy')
    test=[c for s in (471000,472000,473000) for c in LE.cases(s,per=4,afc=True)]
    ctr={}
    for mx in (0.,.25,.5,.75,1.,1.5):
        ctr[f'mix{mx}']=(lambda mx:lambda c:F.synthesize(dict(ep,mix=mx,**c['fit'])))(mx)
    for out in ('advance','avg'):
        ctr[out]=(lambda o:lambda c:F.synthesize(dict(ep,output=o,mix=.5,**c['fit'])))(out)
    def lrn(c):
        m=F.synthesize(dict(ep,**c['fit']));base=S.F.LEVELS[np.array(m['lut'])&63];return AD.with_dac(m,learned)
    ctr['learned a1']=lrn
    rows=AD.evaluate(test,ctr)
    strong=[np.fromfile(f,np.uint8) for f in sorted((Path.home()/'c5vrx4-static-224006').glob('*.bin'))]
    weak=[np.fromfile(f,np.uint8) for f in sorted((Path.home()/'c5vrx4-static-224103').glob('*.bin'))]
    fitc={'fit_deviation':1.23,'fit_centre_hz':1.96e6}
    for n in ctr:
        m=ctr[n]({'fit':fitc});ck=SE.real_click_increase(m,strong,weak)
        g=lambda ks,j:np.mean([v[j] for k in ks for v in rows[(k,n)]])
        print(n.ljust(11),'weak(2-6) miss %2d false %.2f SINAD %.1f | 8-10 SINAD %.1f | strong(13-30) SINAD %.1f | clicks %.2f'%(sum(v[0] for k in (2,4,6) for v in rows[(k,n)]),g((2,4,6),2),g((2,4,6),1),g((8,10),1),g((13,16,20,30),1),ck),flush=True)
