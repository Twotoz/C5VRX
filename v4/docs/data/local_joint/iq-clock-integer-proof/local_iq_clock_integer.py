"""C5VRX/Twotoz streaming integer IQ-clock accumulator, local prototype.

uint32 phase and int32 histogram sums, two hypotheses, no per-pair float,
division, modulo or atan. Histogram scoring remains the frozen float reference
outside the sample loop; no physical C5 CPU/DMA acceptance is implied.
"""
import hashlib
import json
from pathlib import Path
import numpy as np
from numba import njit
import local_iq_clock as R
import local_joint_observation as J


@njit(cache=True)
def accumulate(raw,start,end,stride,next_sample,phase,increment,hist,sums):
    """Streaming global sample positions; chunks may end between pair bytes.

Caller retains the one previous byte when next_sample precedes chunk start.
The proof supplies the same contiguous raw array for chunk invariance. Board
ring ownership and wrap handling are deliberately outside this accumulator.
"""
    k=max(next_sample,start)
    # Normal caller maintains the global sampling lattice; skipping samples
    # requires a separate explicit timestamp-gap protocol, not this shortcut.
    while k+1<end:
        a=int(raw[k]);b=int(raw[k+1])
        ia=a>>4;qa=a&15;ib=b>>4;qb=b&15
        if ia>=8:ia-=16
        if qa>=8:qa-=16
        if ib>=8:ib-=16
        if qb>=8:qb-=16
        ia=2*ia+1;qa=2*qa+1;ib=2*ib+1;qb=2*qb+1
        real=ib*ia+qb*qa;imag=qb*ia-ib*qa
        sums[0]+=real;sums[1]+=imag;sums[2]+=ia*ia+qa*qa;sums[3]+=1
        for t in range(2):
            p=phase[t];bin0=int(p>>25);bin1=(bin0+1)&127
            w1=int((p>>17)&255);w0=256-w1
            hist[t,0,bin0]+=real*w0;hist[t,0,bin1]+=real*w1
            hist[t,1,bin0]+=imag*w0;hist[t,1,bin1]+=imag*w1
            hist[t,2,bin0]+=w0;hist[t,2,bin1]+=w1
            phase[t]=np.uint32((int(p)+int(increment[t]))&0xffffffff)
        k+=stride
    return k


def acquire(raw,chunk=4092):
    stride=63
    periods=list(R.PERIODS.values())
    phase=np.array([round(.5/p*2**32) for p in periods],np.uint32)
    increment=np.array([round(stride/p*2**32) for p in periods],np.uint32)
    hist=np.zeros((2,3,128),np.int32);sums=np.zeros(4,np.int64)
    next_sample=0
    for start in range(0,len(raw),chunk):
        # In a real stream retain any pending A byte across the chunk edge.
        scan_start=min(start,next_sample)
        next_sample=accumulate(raw,scan_start,min(start+chunk,len(raw)),stride,
                              next_sample,phase,increment,hist,sums)
    if not sums[3]:raise ValueError('no sampled pairs')
    global_c=complex(sums[0],sums[1])/(4*sums[3])
    power=sums[2]/(4*sums[3]);choices=[]
    for t,(standard,period) in enumerate(R.PERIODS.items()):
        h=(hist[t,0].astype(float)+1j*hist[t,1])/4
        choices.append(R.score_histogram(h,hist[t,2],global_c,power,
                                       standard,period,stride,int(sums[3])))
    return max(choices,key=lambda p:p['score']),choices,hist,sums


def verify():
    import waveforms as V
    rows=[]
    # Replay all16 fresh stress cases from frozen results. Rebuild exact IQ
    # and verify hashes before comparing; this is a implementation proof,
    # not a new independently seeded performance confirmation.
    frozen=json.loads((J.ROOT/'docs/data/local_joint/iq-clock-stress-validation/results.json').read_text())
    for old in frozen['rows']:
        s=old['spec'];std=s['standard']
        c=V.make_case(std,s['seed'],s['cnr'],4.2,lane_model='ultrafine',
            deviation=s['deviation'],cfo_hz=1e6+s['offset'],stress=s['stress'],
            stimulus_seed=s['seed']+1,pattern='zoneplate' if std=='PAL' else 'checker')
        off=old['crop_raw_bytes'];raw=c['raw'][off:off+240000]
        assert hashlib.sha256(raw.tobytes()).hexdigest()==old['input_sha256']
        data=raw[:old['training_bytes']]
        reference,_=R.acquire(data)
        assert reference==old['estimate']
        p,_,hist,sums=acquire(data)
        one,_,h1,s1=acquire(data,chunk=len(data))
        assert p==one and np.array_equal(hist,h1) and np.array_equal(sums,s1)
        assert p['standard']==reference['standard']
        assert p['lock_declared']==reference['lock_declared']
        error=(p['phase']-reference['phase']+p['period']/2)%p['period']-p['period']/2
        metric=R.timing_metric(p,c['truth'][off:off+240000],len(data),20)
        rows.append(dict(seed=s['seed'],standard=std,phase_difference_ns=float(error)*25,
            fixed_timing=metric,max_abs_hist_sum=int(abs(hist.astype(np.int64)).max()),
            sampled_pairs=int(sums[3]),histogram_bytes=hist.nbytes,chunk_equivalence=True))
        print(json.dumps(rows[-1]),flush=True)
    # Chunk-end cases and worst repeated raw values, independent of video.
    raw=np.random.default_rng(7800190).integers(0,256,80000,dtype=np.uint8)
    base=acquire(raw,chunk=80000)
    for chunk in (1,2,63,64,4092,8191):
        result=acquire(raw,chunk=chunk)
        assert np.array_equal(base[2],result[2]) and np.array_equal(base[3],result[3]),chunk
    result=dict(rows=rows,phase_difference_max_abs_ns=max(abs(r['phase_difference_ns']) for r in rows),
        bounds=dict(max_product_abs=450,max_pairs_2ms=1270,
            worst_bin_abs_bound=450*256*1270,int32_max=2**31-1),
        scope='integer_histogram_equivalence_no_CPU_timing_no_DMA_acceptance',
        source_hashes={Path(p).name:hashlib.sha256(Path(p).read_bytes()).hexdigest()
                       for p in (__file__,R.__file__)})
    target=J.ROOT/'docs/data/local_joint/iq-clock-integer-proof'
    target.mkdir(parents=True,exist_ok=True)
    (target/'proof.json').write_text(json.dumps(result,indent=1))
    for p in (__file__,R.__file__):(target/Path(p).name).write_bytes(Path(p).read_bytes())
    print('DONE',flush=True)


if __name__=='__main__':verify()
