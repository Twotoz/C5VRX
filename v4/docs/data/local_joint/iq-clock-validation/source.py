"""C5VRX/Twotoz direct-IQ horizontal-clock reference, local simulations only.

Coherent delayed-product averaging / cyclostationary detection are prior art.
This estimates H timing from past received IQ without true CVBS, CFO, CNR,
levels or start phase. It is an observer, not a complete CVBS demodulator.
Sampling rate/nominal PAL and NTSC periods and sync width are design priors.
No DMA repair, physical CPU deadline, vertical or colour acceptance proved.
"""
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy import signal as sg
import local_joint_observation as J

FS=40e6
PERIODS={'PAL':2560.,'NTSC':FS*1001/15750000}


def iq(raw):
    def component(x):return np.where(x>=8,x.astype(int)-16,x.astype(int))+.5
    return component(raw>>4)+1j*component(raw&15)


def acquire(raw,stride=63,bins=128):
    # One adjacent25ns product every1.575us:~635k products/s. Coprime to
    # nominal PAL prevents the sparse sampler revisiting only40 phase bins.
    # Every multiply
    # sees full captured IQ4, unlike PAIR's truncated second observation.
    z=iq(raw);ix=np.arange(0,len(raw)-1,stride)
    products=z[ix+1]*z[ix].conjugate()
    global_c=products.mean()
    received_power=float(np.mean(abs(z[ix])**2))
    candidates=[]
    for standard,period in PERIODS.items():
        phase=((ix+.5)%period)*bins/period
        lo=np.floor(phase).astype(int);frac=phase-lo
        h=np.zeros(bins,complex);count=np.zeros(bins)
        for b,w in ((lo,1-frac),((lo+1)%bins,frac)):
            np.add.at(h,b,products*w);np.add.at(count,b,w)
        h/=np.maximum(count,1e-9)
        # Unknown CFO is suppressed by rotation by the received global mean.
        contour=(h*global_c.conjugate()).imag/max(abs(global_c),1e-9)
        # Sync must be lower-frequency than neighbouring front/back porch;
        # no absolute signal level or phase/frequency threshold is supplied.
        scores=np.empty(bins)
        x=np.arange(bins)*period/bins
        for start in range(bins):
            dt=(x-start*period/bins)%period
            pulse=(dt<4.7*40)
            porch=((dt>5.6*40)&(dt<9*40))|(dt>period-1.65*40)
            scores[start]=-np.mean(contour[pulse])+.2*(np.mean(contour[porch])-np.mean(contour[pulse]))
        best=int(np.argmax(scores))
        # Parabolic sub-bin interpolation, bounded to half a bin.
        left,centre,right=scores[(best-1)%bins],scores[best],scores[(best+1)%bins]
        denom=left-2*centre+right
        correction=np.clip(.5*(left-right)/denom,-.5,.5) if denom<0 else 0.
        phase=((best+correction)*period/bins)%period
        confidence=(scores[best]-np.median(scores))/max(np.std(scores),1e-9)
        candidates.append(dict(standard=standard,period=period,phase=phase,
            score=float(scores[best]),confidence=float(confidence),
            normalized_score=float(scores[best])/max(received_power,1e-9),
            lock_declared=bool(scores[best]/max(received_power,1e-9)>=.23),
            products=len(products),histogram_bins=bins,stride=stride))
    return max(candidates,key=lambda p:p['score']),candidates


def timing_metric(pred,truth,start,model_latency_samples=0):
    import goggle_lock as G
    hs=G.true_syncs(truth)[0]
    hs=hs[hs>=start]
    error=(hs-(pred['phase']-model_latency_samples)+pred['period']/2)%pred['period']-pred['period']/2
    return dict(lines=len(hs),median_abs_error_us=float(np.median(abs(error)))/40,
        p95_abs_error_us=float(np.percentile(abs(error),95))/40,
        fraction_within_500ns=float(np.mean(abs(error)<=20)))


def study(seed=7500190,outname='iq-clock-validation'):
    import waveforms as V
    import overlay_fsm as O
    import goggle_lock as G
    rows=[]
    conditions=[(-2,.69,0.,False),(0,.69,0.,False),
                (2,.69,0.,False),(4,.69,0.,False),
                (6,1.2,-2e6,'both'),(30,1.2,2e6,False)]
    train=80000
    for n,(cnr,dev,off,stress) in enumerate(conditions):
        for k,std in enumerate(('PAL','NTSC')):
            sp=dict(seed=seed+101*n+17*k,standard=std,cnr=cnr,
                    deviation=dev,offset=off,stress=stress)
            c=V.make_case(std,sp['seed'],cnr,4.2,lane_model='ultrafine',
                deviation=dev,cfo_hz=1e6+off,stress=stress,
                stimulus_seed=sp['seed']+1,pattern=('zoneplate','checker')[k])
            offset=int(np.random.default_rng(sp['seed']+3).integers(0,240000))
            raw=c['raw'][offset:offset+240000];truth=c['truth'][offset:offset+240000]
            pred,choices=acquire(raw[:train])
            # Labels are used only after freezing the received-only estimate.
            timing=timing_metric(pred,truth,train)
            # The transmitter model contains a linear-phase81-tap6MHz FIR
            # at80MHz:40/80MHz=500ns. Report its declared delay separately;
            # never fit a per-case timing offset against the truth.
            latency_adjusted=timing_metric(pred,truth,train,20)
            m=J.baseline();y=O.decode(raw,m)
            yc=O.decode(c['clean'][offset:offset+240000],m)
            cc=dict(c,raw=raw,truth=truth,region=c['region'][offset:offset+240000])
            baseline=G.measure(y,yc,cc)
            rows.append(dict(spec=sp,crop_raw_bytes=offset,training_bytes=train,
                input_sha256=hashlib.sha256(raw.tobytes()).hexdigest(),estimate=pred,
                choices=choices,timing=timing,correct_standard=pred['standard']==std,
                known_model_FIR_delay_adjusted_timing=latency_adjusted,
                baseline_U85Q=baseline))
            print(json.dumps(dict(spec=sp,estimate=pred,timing=timing,
                baseline_h=min(baseline['h_ok'],baseline['peak_h_ok']))),flush=True)
    # Null controls have no periodic video; report scores rather than pretend
    # that every argmax constitutes a lock. Threshold selection is separate.
    rng=np.random.default_rng(seed+9001);nulls=[]
    from iq_lanes import quantize
    for _ in range(16):
        z=(rng.normal(size=160000)+1j*rng.normal(size=160000))/np.sqrt(2)
        z=O.H.B.D.chan(z)[::2];z*=4.2/np.sqrt(np.mean(abs(z)**2))
        p,_=acquire(quantize(z,'ultrafine'));nulls.append(p)
    result=dict(seed=seed,training_bytes=train,rows=rows,noise_only_controls=nulls,
        detector_inputs='received_raw_IQ_only_design_standard_priors',
        calibration='clock_has_no_truth_fit_U85Q_baseline_has_clean_oracle',
        scope='offline_H_observer_not_complete_demod_not_C5_CPU_or_DMA_acceptance',
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    target=J.ROOT/'docs/data/local_joint'/outname
    target.mkdir(parents=True,exist_ok=True)
    (target/'results.json').write_text(json.dumps(result,indent=1))
    (target/'source.py').write_bytes(Path(__file__).read_bytes())
    print('DONE',flush=True)


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument('--seed',type=int,default=7500190)
    ap.add_argument('--outname',default='iq-clock-validation')
    a=ap.parse_args();study(a.seed,a.outname)
