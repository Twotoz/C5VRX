"""Local C5VRX/Twotoz circular and constrained phase-model ablations.

Closed formulas, unchanged U85Q observation encoder and two-lookup schedule.
These are research variants, not claimed new inventions or physical wins.
Circular averaging is a heuristic, not full Bayesian posterior tracking.
"""
import json
import numpy as np
import compile_overlay as C
import local_joint_observation as J


def build(kind, strength=0., prior_hz=0.):
    m=J.baseline(); original=np.asarray(m['lut'],np.int64)
    ph=(np.arange(32)+.5)*2*np.pi/32-np.pi
    step=2*np.pi/32; prior=prior_hz*2*np.pi/20e6
    lut=np.zeros(1024,np.int64)
    for s in range(32):
        for t in range(32):
            coarse=t>=28
            obs=((t-28+.5)*np.pi/2-np.pi if coarse else
                 (t+.5)*2*np.pi/28-np.pi)
            gain=.5 if coarse else .85
            e=J.wrap(obs-ph[s]-prior)
            if kind=='circular':
                correction=np.angle((1-gain)+gain*np.exp(1j*e))
            elif kind=='cauchy':
                correction=gain*e/(1+strength*(1-np.cos(e)))
            elif kind=='consistent':
                correction=gain*e
            else: raise ValueError(kind)
            nxt=int(np.floor((ph[s]+prior+correction+np.pi)/step))%32
            signed_step=(nxt-s+16)%32-16
            if kind=='consistent':
                # DAC's representable -6..9 MHz must also constrain the
                # maintained phase advance, rather than clipping DAC alone.
                signed_step=int(np.clip(signed_step,-9,14))
                nxt=(s+signed_step)%32
            hz=signed_step*20e6/32
            code=int(np.rint(np.clip((hz+6e6)/15e6*63,0,63)))
            lut[s*32+t]=code|(nxt<<6)
    lut|=original&0xf800
    m.update(name=f'CIRC-{kind}-{strength:g}-prior{prior_hz:g}',lut=lut.tolist())
    m.pop('formula',None)
    m['research']=dict(kind=kind,strength=strength,fixed_prior_hz=prior_hz,
                       physical_acceptance=False,external_runtime_CNR=False)
    C.build(m)
    return m


def candidates():
    return [build('circular'),build('circular',prior_hz=1e6),
            build('cauchy',.15),build('cauchy',.5),
            build('consistent'),build('consistent',prior_hz=1e6)]


def verify():
    import bs_model as BS
    import overlay_fsm as O
    from functools import lru_cache
    old=BS.parse;BS.parse=lru_cache(maxsize=8)(old)
    try:
        raw=np.random.default_rng(6600190).integers(0,256,8192,dtype=np.uint8)
        for m in candidates():
            src=C.build(m)
            _,lut,blocks,_=BS.parse(src)
            assert len(lut)==1024 and len(blocks)==8
            actual=np.asarray(BS.simulate(src,raw,len(raw),wrap_rom=True))&63
            assert np.array_equal(actual,O.model_codes(raw,m))
        return dict(models=6,bytes_per_stream=8192,
                    status='source_dataflow_only_not_board_acceptance')
    finally:BS.parse=old


if __name__=='__main__':print(json.dumps(verify()))
