"""C5VRX/Twotoz local untrained counter-phase experiment.

Extends the existing compile_overlay counter_phase schedule, not a new
discovery of C5 counters. RX40/raw32K/TX40 duplicate DAC6, two bundles.
The low ten counter operand bits are necessarily the raw byte, not free DSP.
We expose and model that address/arithmetic coupling exactly.
"""
import numpy as np
from numba import njit
import compile_overlay as C
import local_joint_observation as J
import overlay_fsm as O


def build(kp=.85, qgain=.5, output='increment'):
    reference=J.baseline();old=np.asarray(reference['lut'],np.int64)
    lut=np.zeros(1024,np.int64)
    phase=(np.arange(32)+.5)*2*np.pi/32-np.pi
    # Expected input address is a declared uniform-byte nuisance approximation,
    # not a runtime true frequency. Actual accumulator uses the exact byte.
    bias=127.5/2048
    for s in range(32):
        for t in range(32):
            angle=(t+.5)*2*np.pi/28-np.pi if t<28 else (t-28+.5)*np.pi/2-np.pi
            gain=kp if t<28 else qgain
            correction=gain*J.wrap(angle-phase[s])
            d=int(np.floor(correction*32/(2*np.pi)-bias+.5))
            units=(d+bias+16)%32-16
            hz=(units*20e6/32) if output=='increment' else correction*20e6/(2*np.pi)
            dac=int(np.rint(np.clip((hz+6e6)/15e6*63,0,63)))
            lut[s*32+t]=dac|((d&31)<<6)
    for raw in range(256):
        pair_address=(raw>>4)|((raw&15)<<4)|768
        lut[raw]|=(old[pair_address]>>11)<<11
    return dict(name=f'COUNTER-LOOP-kp{kp:g}-q{qgain:g}-{output}',
                params=dict(token_bits=5,phases=32,confidence_groups=1,counter_phase=True),
                lut=lut.tolist(),research=dict(kp=kp,qgain=qgain,output=output,
                operand_bias=127.5,scope='local_untrained_not_physical_acceptance'))


@njit(cache=True)
def codes(raw,lut):
    out=np.zeros(len(raw),np.uint8)
    state=0;accumulator=0
    for k in range(len(raw)//2-1):
        address=int(raw[2*k])
        accumulator=(accumulator+address+(state<<11))&65535
        token=int(lut[address])>>11
        value=int(lut[((accumulator>>11)<<5)+token])
        state=(value>>6)&31
        out[2*k+2]=out[2*k+3]=value&63
    return out


def decode(raw,m):
    return O.H.B.D.goggle(O.H.B.DAC_VOLTS[codes(raw,np.asarray(m['lut'],np.uint16))])


def candidates():
    return [build(k,q,o) for k,q,o in [(.85,.5,'increment'),(1.,.5,'increment'),
             (.85,.25,'increment'),(.85,.5,'innovation')]]


def source_checks():
    import bs_model as BS
    raw=np.random.default_rng(6400190).integers(0,256,16384,dtype=np.uint8)
    for m in candidates():
        source=C.build(m)
        actual=np.asarray(BS.simulate(source,raw,len(raw),wrap_rom=True))&63
        expected=codes(raw,np.asarray(m['lut'],np.uint16))
        assert np.array_equal(actual,expected),(m['name'],np.flatnonzero(actual!=expected)[:10])
    return dict(models=4,stream=16384,status='host_dataflow_pass_not_physical_timing')


if __name__=='__main__':
    print(source_checks())
