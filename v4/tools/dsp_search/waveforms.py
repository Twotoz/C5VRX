"""Full interlaced CVBS model for C5VRX by Twotoz/contributors.

Timing follows main/menu_raster.c and ITU-R BT.470/BT.1700. This is synthetic
PAL/NTSC with burst and chroma test patterns, not a certified video generator.
No candidate-specific delay, polarity, amplitude or offset fitting is allowed.
"""
import numpy as np
from scipy import signal as sg
from hardware import B
import weak_signal_sweep as W
import mega_demod_bench as M
FS=40000000


def half_sample(standard,h):
    return h*1280 if standard=='PAL' else ((h*2860+4)//9)*4


def raster(standard,fields=2,short=False,stimulus_seed=None,pattern='bars'):
    if pattern not in ('bars','zoneplate','checker','texture','osd'):raise ValueError('unknown image pattern')
    pal=standard=='PAL';fh=625 if pal else 525;eq=5 if pal else 6
    total=128 if short else fh*fields
    length=half_sample(standard,total)
    ire=np.zeros(length);region=np.zeros(length,dtype=np.uint8)
    fsc=4433618.75 if pal else 3579545.454545
    rng=np.random.default_rng(stimulus_seed) if stimulus_seed is not None else None
    h=0
    while h<total:
        pos=(h+(eq if pal else 0))%fh
        start,end=half_sample(standard,h),half_sample(standard,h+1)
        if pos<3*eq:
            width=(end-start)-188 if eq<=pos<2*eq else (94 if pal else 92)
            ire[start:start+width]=-40;region[start:start+width]=2
            h+=1;continue
        if h&1:h+=1;continue
        end=half_sample(standard,min(h+2,total))
        ire[start:start+188]=-40;region[start:start+188]=2
        bs=start+(224 if pal else 212);be=bs+(90 if pal else 100)
        t=np.arange(bs,be)/FS
        burst_phase=(.75*np.pi if (h//2)&1 else -.75*np.pi) if pal else np.pi
        ire[bs:be]=20*np.sin(2*np.pi*fsc*t+burst_phase);region[bs:be]=3
        if pos>40:
            a,b=start+420,min(end-80,end)
            x=np.arange(b-a)/FS
            bars=np.minimum((x/(max(1,len(x))/FS)*8).astype(int),7)
            levels=np.array([100,75,50,25,0,15,55,85])
            amplitude,detail_hz=8,3e6
            if rng is not None:
                levels=levels.astype(float)
                levels[[1,2,3,5,6,7]]=rng.uniform(10,90,6)
                amplitude=float(rng.uniform(4,10));detail_hz=float(rng.choice([1.2e6,2.2e6,3e6,3.8e6]))
            y=levels[bars].astype(float)
            # Preserve flat black/white patches; other bars test luma/chroma.
            detail=(bars>=5)
            y+=detail*(amplitude*np.sin(2*np.pi*detail_hz*x)+
                       8*np.sin(2*np.pi*fsc*(x+a/FS)+((-1)**(h//2) if pal else 1)*.6))
            if pattern!='bars':
                texture=(bars!=0)&(bars!=4)
                if pattern=='zoneplate':
                    duration=max(len(x)/FS,1/FS)
                    image=50+35*np.sin(2*np.pi*(.25e6*x+4.25e6*x*x/(2*duration))+h*.19)
                elif pattern=='checker':
                    image=5.+90.*((np.floor((np.arange(len(x))+13*h)/(8+4*(h%5))).astype(int))&1)
                elif pattern=='osd':
                    # High-contrast moving C5 glyphs over a textured scene.
                    glyph=np.array([[1,1,1,0,1,1,1],[1,0,0,0,1,0,0],
                                    [1,0,0,0,1,1,1],[1,0,0,0,0,0,1],
                                    [1,1,1,0,1,1,1]],float)
                    row=((h//2)%40)//4; col=((np.arange(len(x))+13*(h//625))//20)%30
                    image=45+18*np.sin(2*np.pi*.9e6*x+h*.1)
                    if row<5:
                        mask=col<7; image[mask]=5+90*glyph[row,col[mask]]
                else:
                    prng=rng if rng is not None else np.random.default_rng(h)
                    knots=np.arange(0,len(x)+8,8)
                    image=np.interp(np.arange(len(x)),knots,prng.uniform(5,95,len(knots)))
                image+=8*np.sin(2*np.pi*fsc*(x+a/FS)+((-1)**(h//2) if pal else 1)*.6)
                y[texture]=image[texture]
            ire[a:b]=y;region[a:b]=1
            region[a:b][bars==0]=4;region[a:b][bars==4]=5
        h+=2
    return ire,region


def make_case(standard,seed,cnr,rms=3,short=False,stress=False,cfo_hz=1e6,stimulus_seed=None,loss_windows_us=(),lane_model=None,include_traces=False,pattern='bars',
              deviation=1.,dc=0j,iq_gain=1.,iq_phase_deg=0.,gain_windows=(),phase_jumps=(),interferers=(),pre_bw_hz=None):
    # Hardware randomization (defaults = unchanged): VTX deviation scale,
    # receiver DC (fraction of RMS), I/Q gain and phase imbalance.
    ire,region=raster(standard,short=short,stimulus_seed=stimulus_seed,pattern=pattern)
    # Integrate FM at 80 MS/s before the existing analog-channel model.
    source=sg.lfilter(sg.firwin(81,6e6,fs=80e6),1,np.repeat(ire,2))
    freq=cfo_hz+deviation*(source-30)*6.7e6/140
    z=np.exp(2j*np.pi*np.cumsum(freq)/80e6)
    z=B.D.chan(z)[::2]
    z/=np.sqrt(np.mean(abs(z)**2))
    rng=np.random.default_rng(seed)
    noise=B.D.chan((rng.normal(size=len(source))+1j*rng.normal(size=len(source)))/np.sqrt(2))[::2]
    noise/=np.sqrt(np.mean(abs(noise)**2))
    if stress:
        t=np.arange(len(z))/FS
        z=(z+.35*np.exp(.7j)*np.pad(z[:-8],(8,0)))*(1-.6*(.5+.5*np.sin(2*np.pi*t/150e-6)))
    # Optional independent confirmation stresses, in the received complex
    # waveform before lane quantization. Existing cases are bit-identical.
    for start_us, angle in phase_jumps:
        if not 0 <= start_us < len(z)/40: raise ValueError('phase jump outside case')
        z[round(start_us*40):] *= np.exp(1j*angle)
    for amplitude, offset_hz, phase in interferers:
        z += amplitude*np.exp(1j*(2*np.pi*offset_hz*np.arange(len(z))/FS+phase))
    for start,end in loss_windows_us:
        if not 0<=start<end<=len(z)/40:raise ValueError('loss interval outside case')
        z[round(start*40):round(end*40)]=0 # Noise remains; fixed gain, not a simulated AGC.
    analog={}
    pre=sg.butter(4,pre_bw_hz/2,fs=FS,output='sos') if pre_bw_hz else None
    def raw(n):
        y=W.iq_at(z,n,cnr,rms)
        if pre is not None:
            # Narrower analog channel filter before the lanes; the gain loop
            # restores the same total amplitude (rms) at the ADC.
            y=sg.sosfilt(pre,y);y*=rms/np.sqrt(np.mean(abs(y)**2))
        if stress:y=y.real*1.05+1j*y.imag+.15-.1j
        if dc or iq_gain!=1. or iq_phase_deg:
            ph=np.radians(iq_phase_deg)
            y=iq_gain*y.real+1j*(y.imag*np.cos(ph)+y.real*np.sin(ph))+dc*rms
        for start_us,end_us,gain in gain_windows:
            if not 0 <= start_us < end_us <= len(y)/40 or gain <= 0: raise ValueError('invalid gain window')
            y[round(start_us*40):round(end_us*40)] *= gain
        analog['y' if n is noise else 'clean']=y.astype(np.complex64)  # unquantized (reference receivers)
        if lane_model:
            from iq_lanes import quantize
            return quantize(y,lane_model)
        return B.D.raw_bytes(y).astype(np.uint8)
    clean=raw(np.zeros_like(noise));truth=B.D.goggle(ire)
    ref=W.decode(clean,M.F.load_reference('OVP56'))
    calibration=M.clean_calibration(ref,truth,3000)
    if calibration[1]<=0:raise ValueError('baseline polarity must be positive')
    result=dict(raw=raw(noise),clean=clean,truth=truth,region=region,calibration=calibration,
                standard=standard,seed=seed,cnr=cnr,rms=rms,stress=stress,stimulus_seed=stimulus_seed,cfo_hz=cfo_hz,
                loss_windows_us=loss_windows_us,lane_model=lane_model,pattern=pattern)
    if include_traces:result.update(rx_signal=z,analog=analog['y'],analog_clean=analog['clean'])
    return result


def calibrated(y,c):
    lag,gain,offset=c['calibration']
    # Constant reference-only calibration; no roll/wrap into another field.
    lo=max(3000,-lag);hi=min(len(y)-3000,len(y)-lag)
    return gain*y[lo+lag:hi+lag]+offset,c['truth'][lo:hi],c['region'][lo:hi]


def pulses(y):
    below=y < -20
    d=np.diff(np.r_[False,below,False].astype(np.int8))
    starts,ends=np.flatnonzero(d==1),np.flatnonzero(d==-1)
    keep=ends-starts>=40 # Ignore sub-us noise crossings, without repairing any.
    return starts[keep],ends[keep]-starts[keep]


def waveform_metrics(y,c):
    y,t,region=calibrated(y,c);error=y-t
    es,ew=pulses(t);actual,width=pulses(y)
    if len(actual):
        j=np.searchsorted(actual,es);j=np.clip(j,0,len(actual)-1)
        prev=np.maximum(j-1,0);j=np.where(abs(actual[prev]-es)<abs(actual[j]-es),prev,j)
        delta=actual[j]-es;ok=abs(delta)<=80
    else:j=np.zeros(len(es),int);delta=np.full(len(es),100000);ok=np.zeros(len(es),bool)
    horizontal=(ew>140)&(ew<320);broad=ew>600
    def level(label):
        mask=region==label
        return float(np.mean(error[mask])) if np.any(mask) else 0.
    def jitter(mask):
        selected=ok&mask
        return float(np.percentile(abs(delta[selected]),95)/40) if np.any(selected) else 1e6
    hmiss=int(np.sum(horizontal&~ok));vmiss=int(np.sum(broad&~ok))
    width_error=float(np.sqrt(np.mean((width[j[ok&horizontal]]-ew[ok&horizontal])**2))/40) if np.any(ok&horizontal) else 1e6
    vstarts=actual[width>600] if len(actual) else np.array([])
    vtrains=int(np.sum(np.diff(vstarts)>8000)+1) if len(vstarts) else 0
    covariance=np.mean((y-y.mean())*(t-t.mean()))
    return dict(sinad=float(10*np.log10(np.var(t)/max(np.mean(error**2),1e-20))),
                contrast=float(100*covariance/max(np.var(t),1e-20)),
                polarity_ok=bool(covariance>0),large_errors=float(1000*np.mean(abs(error)>40)),
                h_missing=hmiss,v_missing=vmiss,v_trains=vtrains,
                h_jitter_us=jitter(horizontal),v_jitter_us=jitter(broad),
                h_width_rmse_us=width_error,sync_error_ire=level(2),
                black_error_ire=level(5),white_error_ire=level(4),
                raw_min=float(np.min(y)),raw_max=float(np.max(y)))


def gate(candidate,reference,strong=True):
    reasons=[]
    if not candidate['polarity_ok']:reasons.append('polarity')
    if strong:
        if candidate['sinad']<reference['sinad']-.25:reasons.append('strong_sinad')
        for k in ('h_missing','v_missing'):
            if candidate[k]>reference[k]:reasons.append(k)
        if candidate['v_trains']!=reference['v_trains']:reasons.append('vertical_trains')
        for k in ('h_jitter_us','v_jitter_us','h_width_rmse_us'):
            if candidate[k]>reference[k]+.1:reasons.append(k)
        for k in ('sync_error_ire','black_error_ire','white_error_ire'):
            if abs(candidate[k])>abs(reference[k])+2:reasons.append(k)
    else:
        if candidate['sinad']<reference['sinad']-.1:reasons.append('weak_sinad')
        if candidate['h_missing']>reference['h_missing'] or candidate['v_missing']>reference['v_missing']:
            reasons.append('weak_sync')
    return reasons
