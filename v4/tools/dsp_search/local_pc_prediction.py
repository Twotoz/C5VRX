"""C5VRX/Twotoz research: mode memory in branch control flow, not LUT state.

Eight slots hold two encoder modes; previous published DAC MSB selects the
next mode. Full32 phase states and32 observation tokens are retained.
Predictive observation rotation is a local linear approximation, not an exact
second-order Bayesian estimator or equivalence at the phase-wrap boundary.
No hardware timing acceptance or firmware promotion.
"""
import json
import numpy as np
from numba import njit
import bs_model as BS
import local_joint_observation as J
import overlay_fsm as O


def build(scale):
    m=J.baseline();lut=np.asarray(m['lut'],np.int64)&0x7ff
    baseline=np.asarray(m['lut'],np.int64)
    for bank in range(4):
        prior=(-1e6 if bank%2==0 else 4e6)*2*np.pi/20e6*scale
        for raw in range(256):
            orig=baseline[(raw>>4)|((raw&15)<<4)]>>11
            if scale==0:token=int(orig)
            else:
                i=raw>>4;q=raw&15
                i=(i-16 if i>=8 else i)+.5
                q=(q-16 if q>=8 else q)+.5
                coarse=abs(complex(i,q))<.8;gain=.5 if coarse else .85
                obs=J.wrap(np.angle(complex(i,q))+(1/gain-1)*prior)
                token=(28+int(np.floor((obs+np.pi)*4/(2*np.pi)))%4 if coarse
                       else int(np.floor((obs+np.pi)*28/(2*np.pi)))%28)
            lut[bank*256+raw]|=token<<11
    m.update(name=f'PC-PRED-scale{scale:g}',lut=lut.tolist())
    m.pop('formula',None)
    m['research']=dict(pc_prediction=True,scale=scale,mode_memory='instruction_path',
        switch='previous_published_DAC_MSB',fixed_priors_hz=[-1e6,4e6],
        approximation='shifted_observation_local_linear_predictive_bias',
        physical_acceptance=False)
    return m


def source(m):
    head='# C5VRX by Twotoz/contributors; local PC prediction, unaccepted.\n'
    head+='cfg prefetch true\ncfg eof_on downstream\ncfg trailing_bytes 0\ncfg lut_width_bits 16\n'
    head+='lut '+' '.join(map(str,m['lut']))+'\n'
    # Every branch destination is a controller: always two bundles/span.
    modes=[0,0,1,1]
    branches=['if O5 controller_2','ifn O5 controller_0',
              'ifn O5 controller_0','if O5 controller_2']
    for k,mode in enumerate(modes):
        head+=f'''controller_{k}:
    set 0..5 L0..L5,
    set 8..13 L0..L5,
    set 16..23 0..7,
    set 24 {'H' if mode else 'L'},
    set 25 L,
    set 26..30 L6..L10,
    read 16,
    write 16,
    nop
worker_{k}:
    set 16..20 L11..L15,
    set 21..25 O26..O30,
    {branches[k]}
'''
    _,lut,blocks,_=BS.parse(head)
    assert len(lut)==1024 and len(blocks)==8
    return head


@njit(cache=True)
def codes(raw,lut):
    out=np.zeros(len(raw),np.uint8)
    state=0;previous_code=0;mode=0
    for k in range(len(raw)//2-1):
        token=int(lut[int(raw[2*k])+256*mode])>>11
        value=int(lut[state*32+token])
        state=(value>>6)&31
        mode=(previous_code>>5)&1
        previous_code=value&63
        out[2*k+2]=out[2*k+3]=previous_code
    return out


def decode(raw,m):
    return O.H.B.D.goggle(O.H.B.DAC_VOLTS[codes(raw,np.asarray(m['lut'],np.uint16))])


def candidates():return [build(x) for x in (0.,.5,1.)]


def verify():
    from functools import lru_cache
    old=BS.parse;BS.parse=lru_cache(maxsize=4)(old)
    try:
        raw=np.random.default_rng(7000190).integers(0,256,8192,dtype=np.uint8)
        for m in candidates():
            stats={}
            actual=np.asarray(BS.simulate(source(m),raw,len(raw),stats=stats,wrap_rom=True))&63
            expected=codes(raw,np.asarray(m['lut'],np.uint16))
            assert np.array_equal(actual,expected),(m['name'],np.flatnonzero(actual!=expected)[:8])
            assert all((pc%2)==(k%2) for k,pc in enumerate(stats['trace']))
            assert stats['bundles']==len(raw)-1
            if m['research']['scale']==0:
                assert np.array_equal(actual,O.model_codes(raw,J.baseline()))
        return dict(models=3,bytes_per_stream=8192,slots=8,lut_bytes=2048,
            alternating_controller_worker=True,zero_scale_byte_exact_U85Q=True,
            status='host_dataflow_only_branches_need_assembler_and_board_timing')
    finally:BS.parse=old


if __name__=='__main__':print(json.dumps(verify()))
