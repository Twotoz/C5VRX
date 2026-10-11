"""C5VRX/Twotoz closed-form two-state velocity prediction, local only.

Second-order PLL/state factorization is prior art. This experiment explicitly
allocates ROM state to phase and frequency instead of learning prototypes.
No CNR, true frequency, raster phase, training data or future samples supplied.
"""
import json
import numpy as np
import compile_overlay as C
import local_joint_observation as J
import overlay_fsm as O


def build(token_bits,hysteresis_hz):
    m=J.baseline();T=1<<token_bits;states=1024//T;P=states//2
    ph=(np.arange(P)+.5)*2*np.pi/P-np.pi
    velocities=np.array([-1e6,4e6]);mid=float(np.mean(velocities))
    za,_=O.pair_cells('4411');ang=np.angle(za);ordinary=T-4
    tokens=np.floor((ang+np.pi)*ordinary/(2*np.pi)).astype(int)%ordinary
    origin=np.abs(za)<.8
    quad=np.floor((ang+np.pi)*4/(2*np.pi)).astype(int)%4
    tokens[origin]=ordinary+quad[origin]
    lut=np.zeros(1024,np.int64)
    for s in range(states):
        mode,p=divmod(s,P);prior=velocities[mode]*2*np.pi/20e6
        for t in range(T):
            coarse=t>=ordinary
            obs=((t-ordinary+.5)*np.pi/2-np.pi if coarse else
                 (t+.5)*2*np.pi/ordinary-np.pi)
            gain=.5 if coarse else .85
            innovation=J.wrap(obs-ph[p]-prior)
            advance=prior+gain*innovation
            # Prediction is modulo phase; frequency emission is not forcibly
            # folded to Nyquist when the declared prior resolves its branch.
            f=advance*20e6/(2*np.pi)
            newmode=mode
            if f>mid+hysteresis_hz:newmode=1
            elif f<mid-hysteresis_hz:newmode=0
            newp=int(np.floor((ph[p]+advance+np.pi)*P/(2*np.pi)))%P
            nxt=newmode*P+newp
            code=int(np.rint(np.clip((f+6e6)/15e6*63,0,63)))
            lut[s*T+t]=code|(nxt<<6)
    lut|=tokens<<(16-token_bits)
    m['params']=dict(m['params'],token_bits=token_bits)
    m.update(name=f'VEL-P{P}-F2-T{T}-h{hysteresis_hz:g}',lut=lut.tolist())
    m.pop('formula',None)
    m['research']=dict(phase_bins=P,frequency_bins_hz=velocities.tolist(),
        hysteresis_hz=hysteresis_hz,physical_acceptance=False,
        limitation='binary_velocity_is_a_model_prior_not_actual_CFO_or_video')
    C.build(m)
    return m


def candidates():
    return [build(b,h) for b in (4,5) for h in (0.,.75e6)]


def verify():
    import bs_model as BS
    from functools import lru_cache
    old=BS.parse;BS.parse=lru_cache(maxsize=4)(old)
    try:
        raw=np.random.default_rng(6800190).integers(0,256,8192,dtype=np.uint8)
        for m in candidates():
            src=C.build(m);_,lut,blocks,_=BS.parse(src)
            assert len(lut)==1024 and len(blocks)==8
            assert np.array_equal(np.asarray(BS.simulate(src,raw,len(raw),
                    wrap_rom=True))&63,O.model_codes(raw,m))
        return dict(models=4,bytes_per_stream=8192,
                    status='host_source_dataflow_only_not_physical_timing')
    finally:BS.parse=old


if __name__=='__main__':print(json.dumps(verify()))
