"""Common fixed-calibration fine-detail and dropout measurements."""
import numpy as np
from scipy import signal as sg
import waveforms as V


def detail(y,c):
    a,t,_=V.calibrated(y,c)
    filt=sg.butter(2,500000,fs=40000000,btype='highpass',output='sos')
    a=sg.sosfilt(filt,a);t=sg.sosfilt(filt,t)
    return float(np.dot(a,t)/np.sqrt(max(np.dot(a,a)*np.dot(t,t),1e-20)))


def dropout(y,c):
    a,t,_=V.calibrated(y,c)
    es,ew=V.pulses(t);actual,_=V.pulses(a);horizontal=(ew>140)&(ew<320)
    expected=es[horizontal]
    if len(actual):
        j=np.clip(np.searchsorted(actual,expected),0,len(actual)-1);prev=np.maximum(j-1,0)
        missing=np.minimum(abs(actual[j]-expected),abs(actual[prev]-expected))>80
    else:missing=np.ones(len(expected),bool)
    longest=current=0
    for failed in missing:
        current=current+1 if failed else 0;longest=max(longest,current)
    broad=ew>600;vstarts=es[broad]
    expected_trains=int(np.sum(np.diff(vstarts)>8000)+1) if len(vstarts) else 0
    def penalized(mask):
        expected=es[mask]
        if not len(expected):return 0.
        if not len(actual):return 2.025
        j=np.clip(np.searchsorted(actual,expected),0,len(actual)-1);prev=np.maximum(j-1,0)
        error=np.minimum(abs(actual[j]-expected),abs(actual[prev]-expected))/40
        return float(np.percentile(np.minimum(error,2.025),95))
    return dict(h_expected=len(expected),max_h_gap=longest,expected_v_trains=expected_trains,
                h_jitter_us=penalized(horizontal),v_jitter_us=penalized(broad))


def waveform(y,c):
    r=V.waveform_metrics(y,c)
    r.update(h_jitter_matched_us=r['h_jitter_us'],v_jitter_matched_us=r['v_jitter_us'])
    r.update(dropout(y,c))
    return r


def relative_quality(r,ref):
    reasons=[]
    if not r['polarity_ok']:reasons.append('polarity')
    if r['sinad']<ref['sinad']-.25:reasons.append('strong_sinad')
    for k in ('h_missing','v_missing'):
        if r[k]>ref[k]:reasons.append(k)
    expected=r['expected_v_trains']
    if abs(r['v_trains']-expected)>abs(ref['v_trains']-expected):reasons.append('vertical_trains')
    for k in ('h_jitter_us','v_jitter_us','h_width_rmse_us'):
        if r[k]>ref[k]+.1:reasons.append(k)
    for k in ('sync_error_ire','black_error_ire','white_error_ire'):
        if abs(r[k])>abs(ref[k])+2:reasons.append(k)
    return reasons


def burst(y,c):
    """Tone projections report colour-burst integrity, without recalibrating y."""
    a,t,region=V.calibrated(y,c);mask=region==3
    edges=np.diff(np.r_[False,mask,False].astype(np.int8))
    starts,ends=np.flatnonzero(edges==1),np.flatnonzero(edges==-1)
    fsc=4433618.75 if c['standard']=='PAL' else 3579545.454545
    ratios=[]
    for lo,hi in zip(starts,ends):
        phase=2*np.pi*fsc*np.arange(lo,hi)/40e6
        basis=np.c_[np.cos(phase),np.sin(phase),np.ones(hi-lo)]
        coeff=np.linalg.lstsq(basis,np.c_[a[lo:hi],t[lo:hi]],rcond=None)[0]
        values=coeff[0]-1j*coeff[1]
        if abs(values[1])>1:ratios.append(values[0]/values[1])
    if not ratios:raise ValueError('no usable burst intervals')
    ratios=np.array(ratios);gain=abs(ratios);angles=np.angle(ratios)
    centre=np.angle(np.mean(np.exp(1j*angles)));delta=(angles-centre+np.pi)%(2*np.pi)-np.pi
    return dict(burst_gain=float(np.median(gain)),burst_phase_deg=float(np.degrees(centre)),
                burst_phase_jitter_deg=float(np.degrees(np.percentile(abs(delta),95))),
                burst_amplitude_jitter_pct=float(np.percentile(abs(gain/np.median(gain)-1)*100,95)),
                burst_rmse_ire=float(np.sqrt(np.mean((a[mask]-t[mask])**2))))
