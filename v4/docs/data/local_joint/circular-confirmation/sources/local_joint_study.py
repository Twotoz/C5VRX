"""Local C5VRX paired formula ablation; preserves negative results.

Existing goggle model uses per-case clean oracle calibration. Its scores
are relative synthetic screens, not blind real goggle or range acceptance.
"""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import local_joint_observation as J


def specs(seed, full):
    # Fixed design screens; confirmation uses another seed and full fields.
    result = []
    conditions = [dict(cnr=4, dev=.69), dict(cnr=6, dev=1.2, stress='both'),
                  dict(cnr=20, dev=1.2, offset=2e6), dict(cnr=30, dev=.69)]
    for i, p in enumerate(conditions):
        for k, std in enumerate(('PAL', 'NTSC')):
            result.append(dict(seed=seed+101*i+17*k, standard=std, rms=4.2,
                               pattern=('zoneplate','checker')[k], full=full, **p))
    return result


def job(sp):
    import waveforms as V
    import overlay_fsm as O
    import goggle_lock as G
    import cliff_study as S
    c = V.make_case(sp['standard'], sp['seed'], sp['cnr'], sp['rms'],
                    short=not sp['full'], lane_model='ultrafine',
                    cfo_hz=1e6+sp.get('offset',0), stimulus_seed=sp['seed']+1,
                    pattern=sp['pattern'], deviation=sp['dev'], stress=sp.get('stress',False),
                    dc=complex(*sp.get('dc',[0.,0.])),iq_gain=sp.get('iq_gain',1.),
                    iq_phase_deg=sp.get('iq_phase_deg',0.))
    digest = hashlib.sha256(c['raw'].tobytes()).hexdigest()
    models = J.candidates()
    if sp.get('counter'):
        import local_counter_loop as K
        models = [J.baseline()]+K.candidates()
    if sp.get('circular'):
        import local_circular_models as R
        models = [J.baseline()]+R.candidates()
    rows = []
    for m in models:
        decode = K.decode if sp.get('counter') and m['params'].get('counter_phase') else O.decode
        y, yc = decode(c['raw'],m), decode(c['clean'],m)
        r = G.measure(y,yc,c)
        rows.append(dict(name=m['name'],spec=sp,input_sha256=digest,**r))
    for n in ('HC50','RANGE32'):
        maker = S.lookup(S.demods(),n)
        kind,m = maker(dict(c,fit=dict(fit_deviation=1.,fit_centre_hz=1e6)))
        y,yc = S.run(kind,m,c['raw']), S.run(kind,m,c['clean'])
        rows.append(dict(name=n,spec=sp,input_sha256=digest,**G.measure(y,yc,c)))
    return rows


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--seed',type=int,default=6200190)
    ap.add_argument('--full',action='store_true')
    ap.add_argument('--counter',action='store_true')
    ap.add_argument('--circular',action='store_true')
    ap.add_argument('--workers',type=int,default=2)
    ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    models=J.candidates(); scenarios=specs(a.seed,a.full)
    if a.counter:
        import local_counter_loop as K
        models=[J.baseline()]+K.candidates()
        for sp in scenarios: sp['counter']=True
    if a.circular:
        if a.counter: ap.error('choose one model family')
        import local_circular_models as R
        models=[J.baseline()]+R.candidates()
        # Test fixed-prior acquisition off-centre and beyond nominal IQ.
        for i,p in enumerate([dict(cnr=6,dev=1.2,offset=-2e6),
                               dict(cnr=4,dev=.69,dc=[.3,-.2]),
                               dict(cnr=20,dev=1.2,iq_gain=1.4,iq_phase_deg=20.)]):
            for k,std in enumerate(('PAL','NTSC')):
                scenarios.append(dict(seed=a.seed+1001+101*i+17*k,standard=std,
                    rms=4.2,pattern=('zoneplate','checker')[k],full=a.full,**p))
        for sp in scenarios:sp['circular']=True
    protocol=dict(seed=a.seed,full=a.full,specs=scenarios,
                  models=models,calibration='per_case_clean_oracle_existing_goggle_model',
                  base='7491a660e26722afbb9dd9e24e69ba381b4f130d',
                  scope='local_only_no_firmware_promotion_no_physical_range_claim')
    protocol['source_hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__),Path(J.__file__),ROOT/'tools/dsp_search/goggle_lock.py',
                  ROOT/'tools/dsp_search/waveforms.py',ROOT/'tools/compile_overlay.py']}
    if a.counter:
        protocol['source_hashes']['local_counter_loop.py']=hashlib.sha256(Path(K.__file__).read_bytes()).hexdigest()
    if a.circular:
        protocol['source_hashes']['local_circular_models.py']=hashlib.sha256(Path(R.__file__).read_bytes()).hexdigest()
    # Archive executable research sources before starting; hashes alone do
    # not preserve older runner versions when another ablation is added.
    snapshot=a.out/'sources';snapshot.mkdir(exist_ok=True)
    sources=[Path(__file__),Path(J.__file__)]
    if a.counter:sources.append(Path(K.__file__))
    if a.circular:sources.append(Path(R.__file__))
    for p in sources:(snapshot/p.name).write_bytes(p.read_bytes())
    (a.out/'protocol.json').write_text(json.dumps(protocol,indent=1))
    t=time.time(); rows=[]
    with mp.get_context('spawn').Pool(a.workers) as pool:
        for batch in pool.imap_unordered(job,protocol['specs']):
            rows.extend(batch)
            (a.out/'rows.json').write_text(json.dumps(rows,indent=1))
            print(f'{len(rows)}/{len(protocol["specs"])*(len(models)+2)} rows {time.time()-t:.1f}s',flush=True)
    summary={}
    for n in sorted(set(r['name'] for r in rows)):
        rs=[r for r in rows if r['name']==n]
        summary[n]={}
        for group,pred in [('weak',lambda r:r['spec']['cnr']<10),
                           ('strong',lambda r:r['spec']['cnr']>=10),('all',lambda r:True)]:
            rr=[r for r in rs if pred(r)]
            summary[n][group]=dict(picture=float(np.mean([min(r['h_ok'],r['peak_h_ok']) for r in rr])),
                                  sinad=float(np.mean([r['sinad'] for r in rr])),
                                  vertical=float(np.mean([min(r['v_ok'],r['peak_v_ok']) for r in rr])))
    (a.out/'summary.json').write_text(json.dumps(summary,indent=1))
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
    main()
