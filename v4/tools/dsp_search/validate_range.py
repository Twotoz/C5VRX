#!/usr/bin/env python3
"""Freeze three range-first options; confirm against RANGE32 with fresh signals."""
import argparse
import json
from pathlib import Path
import numpy as np
import engine as S
import overlay_fsm as O
import range_objective as J
import validate_search as Q
import validate_overlay as V
import waveforms as W
from video_metrics import burst
from validate_recovery import recovery

CNRS=(0,2,4,6,8,10,12,14,18,22,30)
PINNED=json.loads((Path(__file__).parents[1]/'range32_model.json').read_text())


def evaluate(models,profile,seed,cnrs,stage,stress=False,rms=3,cfo_hz=1e6,loss_windows_us=()):
    ref=S.F.load_reference('OVP56')
    methods=[('OVP56',lambda raw:S.decode(raw,ref)),('RANGE32',lambda raw:O.decode(raw,PINNED))]+V.controls()
    methods+=[(m['name'],lambda raw,m=m:O.decode(raw,m)) for m in models];rows=[]
    for standard in ('PAL','NTSC'):
        for cnr in cnrs:
            c=W.make_case(standard,seed,cnr,rms,stress=stress,cfo_hz=cfo_hz,
                          stimulus_seed=seed+100000,loss_windows_us=loss_windows_us,lane_model='fine')
            c['calibration']=W.M.clean_calibration(S.decode(c['clean'],ref)[:131072],c['truth'][:131072],3000)
            reference=J.measure(O.decode(c['raw'],PINNED),c)
            for name,fn in methods:
                y=fn(c['raw']);r=J.measure(y,c)
                failures=J.strong_failures(r,profile,reference if stress or rms<2 else None) if cnr>=20 and not loss_windows_us else []
                r.update(model=name,profile=profile,stage=stage,seed=seed,standard=standard,cnr=cnr,rms=rms,
                         cfo_hz=cfo_hz,quality_failures=';'.join(failures),usable=J.usable(r))
                if cnr>=20:r.update(burst(y,c))
                else:r.update(burst_gain=None,burst_phase_deg=None,burst_phase_jitter_deg=None,
                              burst_amplitude_jitter_pct=None,burst_rmse_ire=None)
                if loss_windows_us:
                    recovered=[recovery(y,c,end) for _,end in loss_windows_us]
                    locks=[x['first_five_lock_us'] for x in recovered]
                    r.update(recovery_missing=sum(x['missing_first20'] for x in recovered),
                             recovery_valid_lines=sum(x['valid_lines'] for x in recovered),
                             recovery_max_lock_us=max(locks) if all(x is not None for x in locks) else None)
                rows.append(r)
            print('RANGE_FRAME',profile,stage,standard,cnr,seed,flush=True)
    return rows


def aggregate(rows):
    return {name:J.summary([r for r in rows if r['model']==name]) for name in sorted({r['model'] for r in rows})}


def rank(r):
    return (r['usable_cnr'] if r['usable_cnr'] is not None else 999,r['weak_missing'],-r['weak_luma_sinad'])


def confirms(candidate,control):
    # Same usable definition and new independent data for both. Weak luma gain
    # alone cannot qualify when threshold or synchronization deteriorates.
    threshold=candidate['usable_cnr'];old=control['usable_cnr']
    return (candidate['strong_eligible'] and threshold is not None and
            (old is None or threshold<=old) and candidate['weak_missing']<=control['weak_missing'] and
            (old is None or threshold<old or candidate['weak_missing']<control['weak_missing']))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--search',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    if a.output.exists():ap.error('fresh independent confirmation directory required')
    a.output.mkdir(parents=True);p=json.loads((a.search/'protocol.json').read_text());profile=p['profile']
    S.save(a.output/'protocol.json',dict(p,confirmation_lane='fine',common_cnr_grid=CNRS,
         decision='no automatic promotion; freeze once; no runner-up after final veto',
         controls='OVP56,current RANGE32,HC50,original/repaired PLL96,floating IQ40 PLL'))
    models=[]
    for r in json.loads((a.search/'frozen.json').read_text())['finalists']:
        m=r['model'];O.compile_model(m);m['name']='RNG-'+r['id'][:12];models.append(m)
    rows=evaluate(models,profile,p['selection_seed'],(4,6,8,10,12,14,18,22,30),'selection')
    # Offset/occupancy screens enter selection before freezing, not after a
    # nominal winner has already been chosen from those same tests.
    for i,(rms,cfo) in enumerate(((1.5,1e6),(5,1e6),(3,.5e6),(3,1.5e6))):
        rows+=evaluate(models,profile,p['selection_seed']+50+i,(30,),'selection_envelope',rms=rms,cfo_hz=cfo)
    Q.write_rows(a.output/'selection.csv',rows);selected=aggregate(rows)
    eligible=[m for m in models if selected[m['name']]['strong_eligible']]
    winner=min(eligible,key=lambda m:rank(selected[m['name']])) if eligible else None
    S.save(a.output/'frozen_winner.json',dict(winner=winner,selection=selected,no_reselection=True))
    chosen=[winner] if winner else [];final=[]
    for seed in p['final_seeds']:final+=evaluate(chosen,profile,seed,CNRS,'final')
    Q.write_rows(a.output/'final.csv',final);nominal=aggregate(final)
    channel=evaluate(chosen,profile,p['stress_seed'],(4,6,8,10,12,14,18,22,30),'echo_fade',True)
    Q.write_rows(a.output/'channel.csv',channel);stressed=aggregate(channel)
    envelope=[]
    for i,(rms,cfo) in enumerate(((1.5,1e6),(5,1e6),(3,.5e6),(3,1.5e6))):
        envelope+=evaluate(chosen,profile,p['stress_seed']+10+i,(6,10,30),'envelope',rms=rms,cfo_hz=cfo)
    Q.write_rows(a.output/'envelope.csv',envelope)
    loss=evaluate(chosen,profile,p['stress_seed']+30,(6,10,30),'carrier_outage',
                  loss_windows_us=((7000,8000),(26000,27000)))
    Q.write_rows(a.output/'outage.csv',loss)
    accepted=bool(winner and confirms(nominal[winner['name']],nominal['RANGE32']))
    if winner:
        new=stressed[winner['name']];old=stressed['RANGE32']
        accepted &= (new['strong_eligible'] and new['weak_missing']<=old['weak_missing'] and
                     new['weak_luma_sinad']>=old['weak_luma_sinad']-.25)
        if old['usable_cnr'] is not None:
            accepted &= new['usable_cnr'] is not None and new['usable_cnr']<=old['usable_cnr']
        accepted &= not any(r['quality_failures'] for r in envelope if r['model']==winner['name'] and r['cnr']>=20)
        # Lost-carrier intervals themselves are intentionally undecodable.
        # Compare only post-return lines for the hard recovery veto.
        for r in loss:
            if r['model']!=winner['name']:continue
            ref=next(x for x in loss if x['model']=='RANGE32' and x['standard']==r['standard'] and x['cnr']==r['cnr'])
            if r['recovery_missing']>ref['recovery_missing']:accepted=False
            if ref['recovery_max_lock_us'] is not None and (r['recovery_max_lock_us'] is None or
                    r['recovery_max_lock_us']>ref['recovery_max_lock_us']+64):accepted=False
    # Preserve even vetoed options as research; only confirmed models receive
    # the confirmation label. Physical picture/range acceptance stays separate.
    if winner:
        S.save(a.output/'experimental_model.json',winner)
        (a.output/'experimental.bsasm').write_text(O.compile_model(winner))
    if accepted:S.save(a.output/'confirmed_model.json',winner)
    S.save(a.output/'summary.json',dict(profile=profile,selected=winner['name'] if winner else None,
        confirmed_range_improvement=bool(accepted),final=nominal,echo_fade=stressed,
        physical_video_acceptance=False,old_quality_guards_replaced_only_in_this_explicit_range_protocol=True))


if __name__=='__main__':main()
