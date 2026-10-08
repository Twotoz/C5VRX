"""C5VRX range-first screens: detail is negotiable; real video information is not.

New objectives require fresh confirmation. These do not replace the historical
RANGE32 quality guards or turn simulated C/N into measured RF sensitivity.
"""
import numpy as np
from scipy import signal as sg
import waveforms as V
from video_metrics import waveform,detail,burst

PROFILES={
    'balanced':dict(strong_sinad=12,contrast=(70,130),detail=.4,level_error=10,timing=.5,width=.35),
    'range':dict(strong_sinad=9,contrast=(60,135),detail=.25,level_error=15,timing=.6,width=.5),
    'extreme':dict(strong_sinad=7,contrast=(50,140),detail=.1,level_error=20,timing=.75,width=.65),
    'safe_range':dict(strong_sinad=14,contrast=(85,115),detail=.65,level_error=5,timing=.35,width=.2,
                      reference_guard=True,reference_loss=1,detail_loss=.03,
                      weak_width_weight=2,weak_jitter_weight=2,fine_lane=True,burst_guard=True),
}
LOWPASS=sg.butter(2,700000,fs=40000000,output='sos')


def measure(y,c,include_burst=False):
    r=waveform(y,c)
    a,t,_=V.calibrated(y,c)
    a=sg.sosfilt(LOWPASS,a);t=sg.sosfilt(LOWPASS,t)
    # Drop filter startup, without fitting detector-specific gain or delay.
    a=a[400:];t=t[400:]
    r['luma_sinad']=float(10*np.log10(np.var(t)/max(np.mean((a-t)**2),1e-20)))
    r['detail_corr']=detail(y,c)
    if include_burst:r.update(burst(y,c))
    return r


def strong_failures(r,profile,reference=None):
    p=PROFILES[profile];reasons=[]
    if not r['polarity_ok'] or not p['contrast'][0]<=r['contrast']<=p['contrast'][1]:reasons.append('contrast')
    if r['sinad']<p['strong_sinad']:reasons.append('waveform')
    if r['detail_corr']<p['detail']:reasons.append('detail_floor')
    if r['h_missing'] or r['v_missing'] or r['v_trains']!=r['expected_v_trains']:reasons.append('sync')
    for key,limit in [('h_jitter_us',p['timing']),('v_jitter_us',p['timing']),('h_width_rmse_us',p['width'])]:
        if r[key]>limit:reasons.append(key)
    for key in ('sync_error_ire','black_error_ire','white_error_ire'):
        if abs(r[key])>p['level_error']:reasons.append(key)
    # Under adverse channels, never require an already broken reference to
    # meet healthy-source absolutes. Still reject additional sync/level loss.
    if reference is not None:
        if not p.get('reference_guard') or strong_failures(reference,profile):reasons=[]
        if not r['polarity_ok'] or r['contrast']<50:reasons.append('contrast')
        if r['sinad']<reference['sinad']-p.get('reference_loss',2):reasons.append('stress_waveform')
        if p.get('reference_guard') and r['detail_corr']<reference['detail_corr']-p['detail_loss']:
            reasons.append('reference_detail')
        if p.get('burst_guard'):
            if not .7*reference['burst_gain']<=r['burst_gain']<=1.3*reference['burst_gain']:
                reasons.append('burst_gain')
            for key,margin in (('burst_phase_jitter_deg',5),('burst_amplitude_jitter_pct',10),('burst_rmse_ire',3)):
                if r[key]>reference[key]+margin:reasons.append(key)
        for key in ('h_missing','v_missing'):
            if r[key]>reference[key]:reasons.append(key)
        if abs(r['v_trains']-r['expected_v_trains'])>abs(reference['v_trains']-r['expected_v_trains']):reasons.append('vertical_trains')
        for key in ('sync_error_ire','black_error_ire','white_error_ire'):
            if abs(r[key])>max(p['level_error'],abs(reference[key])+5):reasons.append(key)
    return reasons


def usable(r):
    """Same minimum range-picture screen for every profile and control."""
    return (r['polarity_ok'] and 50<=r['contrast']<=150 and r['sinad']>=5 and
            r['h_missing']<=int(.02*r['h_expected']) and r['max_h_gap']<=3 and
            r['v_missing']==0 and r['v_trains']==2 and
            r['h_jitter_us']<=.5 and r['v_jitter_us']<=.5 and r['h_width_rmse_us']<=.5)


def summary(rows):
    weak=[r for r in rows if r['cnr']<=10];strong=[r for r in rows if r['cnr']>=20]
    def mean(rr,key):return float(np.mean([r[key] for r in rr])) if rr else None
    cnrs=sorted({r['cnr'] for r in rows})
    thresholds=[cnr for cnr in cnrs if all(r['usable'] for r in rows if r['cnr']>=cnr)]
    return dict(usable_cnr=min(thresholds) if thresholds else None,
                strong_eligible=bool(strong) and all(not r['quality_failures'] for r in strong),
                weak_missing=sum(r['h_missing']+r['v_missing'] for r in weak),
                weak_sinad=mean(weak,'sinad'),weak_luma_sinad=mean(weak,'luma_sinad'),
                strong_sinad=mean(strong,'sinad'),strong_detail=mean(strong,'detail_corr'))
