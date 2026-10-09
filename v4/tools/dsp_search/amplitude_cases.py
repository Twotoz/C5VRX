"""C5VRX paired amplitude sweeps with fixed nominal voltage calibration.

Requantize identical physical signal/noise traces. Never derive calibration
from a folded amplitude or refit gain to hide distortion.
"""
import numpy as np
import waveforms as V
from iq_lanes import quantize


def cases(seed=1310190, amplitudes=(1.5,3.,4.8,7.,10.), cnrs=(4,8,20,30), per=2, size=32768):
    for cnr in cnrs:
        for k in range(per):
            s=seed+37*cnr+k
            dev=(.69,1.)[k%2];centre=-436e3+dev*1436e3
            c=V.make_case(('PAL','NTSC')[k%2],s,cnr,3.,short=True,cfo_hz=centre,
                          stimulus_seed=s+1,lane_model='ultrafine',include_traces=True,
                          deviation=dev,pattern=('zoneplate','checker','texture','osd')[k%4])
            z=c.pop('rx_signal');rng=np.random.default_rng(s)
            n=V.B.D.chan((rng.normal(size=2*len(z))+1j*rng.normal(size=2*len(z)))/np.sqrt(2))[::2]
            n/=np.sqrt(np.mean(abs(n)**2));zero=np.zeros_like(n)
            # Assert generator reconstruction, before sweeping or cropping.
            assert np.array_equal(quantize(V.W.iq_at(z,n,cnr,3.),'ultrafine'),c['raw'])
            for amp in amplitudes:
                cc=dict(c,rms=amp,fit=dict(fit_deviation=dev,fit_centre_hz=centre))
                cc['raw']=quantize(V.W.iq_at(z,n,cnr,amp),'ultrafine')
                cc['clean']=quantize(V.W.iq_at(z,zero,cnr,amp),'ultrafine')
                for key in ('raw','clean','truth','region'):cc[key]=cc[key][65536:65536+size]
                yield cc
