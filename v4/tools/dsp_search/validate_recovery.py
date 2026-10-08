#!/usr/bin/env python3
"""Full-field RF-loss/state-recovery diagnostic at fixed IQ gain; not AGC proof."""
import argparse
import json
from pathlib import Path
import numpy as np
import engine as S
import overlay_fsm as O
import validate_search as Q
import waveforms as V


def recovery(y,c,end_us,timing_us=.35,width_us=.25):
    a,t,_=V.calibrated(y,c);lo=max(3000,-c['calibration'][0])
    expected,width=V.pulses(t);actual,aw=V.pulses(a)
    mask=(width>140)&(width<320)&(expected+lo>=round(end_us*40))
    expected=expected[mask][:20];width=width[mask][:20]
    if not len(actual):return dict(first_five_lock_us=None,missing_first20=len(expected),valid_lines=0)
    j=np.clip(np.searchsorted(actual,expected),0,len(actual)-1);prev=np.maximum(j-1,0)
    j=np.where(abs(actual[prev]-expected)<abs(actual[j]-expected),prev,j)
    good=(abs(actual[j]-expected)<=round(timing_us*40))&(abs(aw[j]-width)<=round(width_us*40))
    lock=None
    for k in range(max(0,len(good)-4)):
        if np.all(good[k:k+5]):lock=float((expected[k]+lo)/40-end_us);break
    return dict(first_five_lock_us=lock,missing_first20=int(np.sum(abs(actual[j]-expected)>80)),
                valid_lines=int(np.sum(good)))


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--model',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    if a.output.exists():ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True);m=json.loads(a.model.read_text());ref=S.F.load_reference('OVP56');rows=[]
    windows=((7000,8000),(26000,27000))
    for standard in ('PAL','NTSC'):
        for cnr in (6,10,30):
            c=V.make_case(standard,18601,cnr,3,stimulus_seed=118601,loss_windows_us=windows)
            clean=S.decode(c['clean'],ref)
            c['calibration']=V.M.clean_calibration(clean[:131072],c['truth'][:131072],3000)
            for name,y in [('OVP56',S.decode(c['raw'],ref)),(m['name'],O.decode(c['raw'],m))]:
                for _,end in windows:
                    r=recovery(y,c,end);r.update(model=name,standard=standard,cnr=cnr,recovery_start_us=end)
                    rows.append(r)
    Q.write_rows(a.output/'recovery.csv',rows)
    S.save(a.output/'protocol.json',dict(seed=18601,stimulus_seed=118601,loss_windows_us=windows,
          scope='fixed-IQ-gain carrier outage; no RF AGC, board/goggle or usable-range acceptance',
          lock='first five consecutive H pulses within0.35us timing/0.25us width among first20 recovered lines'))
    print('Recovery diagnostic:',len(rows),'matched cases')


if __name__=='__main__':main()
