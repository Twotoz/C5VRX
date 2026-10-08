#!/usr/bin/env python3
"""Independent colour-burst, actual capture-bit and tracking diagnostics."""
import argparse
import json
from pathlib import Path
import numpy as np
import engine as S
import overlay_fsm as O
import validate_search as Q
import waveforms as V
from video_metrics import waveform,burst


def colour_gate(r,ref):
    reasons=[]
    if abs(r['burst_gain']-ref['burst_gain'])>.1:reasons.append('burst_gain')
    if r['burst_phase_jitter_deg']>max(15,ref['burst_phase_jitter_deg']+5):reasons.append('burst_phase_jitter')
    if r['burst_amplitude_jitter_pct']>max(25,ref['burst_amplitude_jitter_pct']+5):reasons.append('burst_amplitude_jitter')
    if r['burst_rmse_ire']>ref['burst_rmse_ire']+2:reasons.append('burst_rmse')
    return reasons


def tracking(c,codes,m=None):
    z=c['rx_signal'];n=len(z);pairs=n//2-1
    expected=np.zeros(n);frequency=np.zeros(pairs);frequency[0]=c['cfo_hz']
    frequency[1:]=np.angle(z[2:-2:2]*z[:-4:2].conj())*20e6/(2*np.pi)
    expected[2:]=np.repeat(frequency,2)
    level=S.F.LEVELS[codes]
    hz=(level/S.H.B.P.SCALE+S.H.B.P.OFFSET)*20e6/256
    error=S.H.B.D.goggle(hz-expected)[3000:-3000]
    result=dict(frequency_bias_hz=float(np.mean(error)),frequency_rmse_hz=float(np.sqrt(np.mean(error**2))),
                frequency_error95_hz=float(np.percentile(abs(error),95)),phase_tracker_rmse_deg=None)
    if m:
        p=m['params']
        if p['phases']!=32 or p.get('counter_phase'):raise ValueError('phase-state diagnostic requires phase32')
        ix=O.indices(c['raw'],np.array(m['lut'],np.uint16),p['token_bits'],p.get('context_bits',0))
        phase=((np.array(m['lut'])[ix]>>6)&31)*2*np.pi/32
        difference=(phase-np.angle(z[::2][:pairs])+np.pi)%(2*np.pi)-np.pi
        result['phase_tracker_rmse_deg']=float(np.degrees(np.sqrt(np.mean(difference[1500:-1500]**2))))
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--model',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    if a.output.exists():ap.error('fresh evidence directory required')
    a.output.mkdir(parents=True);m=json.loads(a.model.read_text());baseline=S.F.load_reference('OVP56');rows=[]
    S.save(a.output/'protocol.json',dict(seed=24601,stimulus_seed=124601,
        strong_burst_guards='gain within0.1 OVP; phase jitter<=max15deg/ref+5; amplitude jitter<=max25%/ref+5; RMSE<=ref+2IRE',
        scope='bit-accurate lane extraction after simplified signed-Q10 ADC; not calibrated RF/AGC or physical colour acceptance'))
    profiles=((3,1e6,10),(3,1e6,30),(5,1e6,10),(5,1e6,30),(3,.5e6,30),(3,1.5e6,30))
    for standard in ('PAL','NTSC'):
        for lane in ('coarse','fine'):
            for rms,cfo,cnr in profiles:
                c=V.make_case(standard,24601,cnr,rms,cfo_hz=cfo,stimulus_seed=124601,
                              lane_model=lane,include_traces=True)
                clean=S.decode(c['clean'],baseline)
                c['calibration']=V.M.clean_calibration(clean[:131072],c['truth'][:131072],3000)
                ref_y=S.decode(c['raw'],baseline);reference=burst(ref_y,c)
                methods=[('OVP56',ref_y,S.codes(c['raw'],baseline),None),
                         (m['name'],O.decode(c['raw'],m),O.codes(c['raw'],np.array(m['lut'],np.uint16),
                          m['params']['token_bits'],m['params'].get('context_bits',0)),m)]
                for name,y,codes,model in methods:
                    r=waveform(y,c);r.update(burst(y,c));r.update(tracking(c,codes,model))
                    r.update(model=name,standard=standard,lane=lane,cnr=cnr,rms=rms,cfo_hz=cfo,
                             colour_failures=';'.join(colour_gate(r,reference)) if cnr>=20 else '')
                    rows.append(r)
    Q.write_rows(a.output/'integrity.csv',rows)
    failures=[r for r in rows if r['model']==m['name'] and r['colour_failures']]
    S.save(a.output/'summary.json',dict(strong_colour_pass=not failures,failures=failures,physical_validation=False))
    print('Signal integrity:',len(rows),'rows; strong colour pass:',not failures)


if __name__=='__main__':main()
