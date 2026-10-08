"""Frozen full-field C5 tracker selection and independent range-edge tests."""
import argparse
import json
from pathlib import Path
import numpy as np
from scipy import signal as sg
import engine as S
import overlay_fsm as O
import search_overlay as R
import validate_search as Q
import waveforms as V
from video_metrics import detail,waveform,relative_quality


def controls():
    fixed=json.loads((Q.G.ROOT/'tools/pll96_model.json').read_text())
    table,enc=Q.G.tables(fixed['params']);lut=np.array(table+enc,np.int64)
    return Q.controls()+[('PLL96-board-fixed',lambda raw:Q.H.B.D.goggle(Q.H.B.DAC_VOLTS[Q.phase_loop(raw,lut)]))]


def evaluate(models,seed,cnrs,stage,stress=False,rms=3,cfo_hz=1e6,stimulus_seed=None):
    rows=[];baseline=S.F.load_reference('OVP56');vlp=S.F.load_reference('VLP56')
    methods=[('OVP56',lambda raw:S.decode(raw,baseline)),('VLP56',lambda raw:S.decode(raw,vlp))]+controls()
    methods += [(m['name'],lambda raw,m=m:O.decode(raw,m)) for m in models]
    for standard in ('PAL','NTSC'):
        for cnr in cnrs:
            c=V.make_case(standard,seed,cnr,rms,stress=stress,cfo_hz=cfo_hz,stimulus_seed=stimulus_seed)
            c['calibration']=V.M.clean_calibration(S.decode(c['clean'],baseline)[:131072],c['truth'][:131072],3000)
            ref=waveform(S.decode(c['raw'],baseline),c);refdetail=detail(S.decode(c['raw'],baseline),c)
            for name,fn in methods:
                y=fn(c['raw']);r=waveform(y,c);r['detail_corr']=detail(y,c)
                failures=(relative_quality(r,ref) if stress or rms<2 else R.quality(r,ref)) if cnr>=20 else []
                if cnr>=20 and r['detail_corr']<refdetail-.03:failures.append('fine_detail')
                r.update(model=name,seed=seed,standard=standard,cnr=cnr,stage=stage,rms=rms,cfo_hz=cfo_hz,
                         quality_failures=';'.join(failures),usable=R.usability(r))
                rows.append(r)
            print('OVERLAY_FRAME',stage,standard,cnr,'methods',len(methods),flush=True)
    return rows


def aggregate(rows):
    result={}
    for name in sorted({r['model'] for r in rows}):
        rr=[r for r in rows if r['model']==name];weak=[r for r in rr if r['cnr']<=10]
        good=[]
        for cnr in sorted({r['cnr'] for r in rr}):
            tests=[r for r in rr if r['cnr']>=cnr]
            if all(r['usable'] for r in tests):good.append(cnr)
        result[name]=dict(strong_eligible=all(not r['quality_failures'] for r in rr),
                          usable_cnr=min(good) if good else None,
                          weak_sinad=float(np.mean([r['sinad'] for r in weak])),
                          weak_missing=int(sum(r['h_missing']+r['v_missing'] for r in weak)),
                          failures=sorted({f for r in rr for f in r['quality_failures'].split(';') if f}))
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--search',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    if a.output.exists():ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True)
    frozen=json.loads((a.search/'frozen.json').read_text())
    protocol=json.loads((a.search/'protocol.json').read_text())
    def test(models,seed,*args,**kwargs):
        return evaluate(models,seed,*args,**kwargs,
                        stimulus_seed=seed+100000 if protocol.get('stimulus_variation') else None)
    models=[]
    for entry in frozen['finalists']:
        m=entry.get('model') or O.synthesize(entry['params'])
        O.compile_model(m) # Learned tables must pass the same executable resource gate.
        m['name']='OVL-'+entry['id'][:12];models.append(m)
    rows=test(models,protocol['selection_seed'],(4,6,8,10,14,18,22,30),'selection')
    if protocol.get('selection_envelope'):
        for i,(rms,cfo) in enumerate(((1.5,1e6),(5,1e6),(3,.5e6),(3,1.5e6))):
            rows+=test(models,protocol['selection_seed']+50+i,(30,),'selection_envelope',rms=rms,cfo_hz=cfo)
        rows+=test(models,protocol['selection_seed']+54,(30,),'selection_channel',True)
    Q.write_rows(a.output/'selection.csv',rows);selection=aggregate(rows)
    def rank(m):
        r=selection[m['name']]
        return (r['usable_cnr'] if r['usable_cnr'] is not None else 999,r['weak_missing'],-r['weak_sinad'])
    eligible=[m for m in models if selection[m['name']]['strong_eligible']]
    winner=min(eligible,key=rank) if eligible else None
    # Freeze before examining either final seed. A failure retains OVP56.
    S.save(a.output/'frozen_winner.json',dict(winner=winner,selection=selection,no_reselection=True))
    final=[]
    for seed in protocol['final_seeds']:final+=test([winner] if winner else [],seed,(0,4,6,8,10,14,18,22,30),'final')
    Q.write_rows(a.output/'final.csv',final);fs=aggregate(final)
    stress=test([winner] if winner else [],protocol['stress_seed'],(4,6,8,10,14,18,22,30),'echo_fade',True)
    Q.write_rows(a.output/'channel.csv',stress);cs=aggregate(stress)
    def improves(a,b,channel=False):
        threshold=a['usable_cnr'];old=b['usable_cnr']
        return (a['strong_eligible'] and a['weak_missing']<=b['weak_missing'] and
                a['weak_sinad']>=b['weak_sinad']-.1 and (threshold is not None or (channel and old is None)) and
                (old is None or (threshold is not None and threshold<=old)) and
                (old is None or threshold<old or a['weak_missing']<b['weak_missing'] or
                 a['weak_sinad']>b['weak_sinad']+.05))
    accepted=bool(winner and improves(fs[winner['name']],fs['OVP56']) and improves(cs[winner['name']],cs['OVP56'],True))
    envelope=[]
    if winner:
        # Additional frozen tests, never used to reselect a runner-up.
        for i,(rms,cfo) in enumerate(((1.5,1e6),(5,1e6),(3,.5e6),(3,1.5e6))):
            envelope+=test([winner],protocol['stress_seed']+10+i,(6,10,30),'occupancy_cfo',rms=rms,cfo_hz=cfo)
        Q.write_rows(a.output/'envelope.csv',envelope)
        for r in envelope:
            if r['model']==winner['name'] and r['cnr']>=20 and r['quality_failures']:accepted=False
    result=dict(selected=winner['name'] if winner else None,confirmed=accepted,
                final=fs,echo_fade=cs,physical_video_acceptance=False,
                decision='experimental candidate; physical validation still required' if accepted else 'retain OVP56; no range replacement confirmed')
    if accepted:
        S.save(a.output/'confirmed_model.json',winner)
        (a.output/'confirmed.bsasm').write_text(O.compile_model(winner))
    S.save(a.output/'summary.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':main()
