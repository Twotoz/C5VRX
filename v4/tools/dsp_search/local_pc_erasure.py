"""C5VRX/Twotoz exact predictive update for near-origin IQ observations.

GPL-3.0-only. Reuses the eight-slot PC schedule, not a new engine.
24 ordinary angles plus quadrant x two prior modes;32 persistent phases.
Prior mode comes from a delayed published DAC bit, not true frequency.
Unlike shifted-observation approximation, coarse prediction wraps innovation
around the predicted phase and retains the frequency branch when emitting DAC.
No physical timing or performance improvement is claimed.
"""
import numpy as np
import local_pc_prediction as P
import local_joint_observation as J
import bs_model as BS
decode=P.decode


def build(low,high,gain):
    m=J.baseline()
    lut=np.zeros(1024,np.int64)
    phases=(np.arange(32)+.5)*2*np.pi/32-np.pi
    for state in range(32):
        for token in range(32):
            if token<24:
                angle=(token+.5)*2*np.pi/24-np.pi
                advance=.85*J.wrap(angle-phases[state])
            else:
                mode,quadrant=divmod(token-24,4)
                angle=(quadrant+.5)*np.pi/2-np.pi
                prior=(low,high)[mode]*2*np.pi/20e6
                advance=prior+gain*J.wrap(angle-phases[state]-prior)
            nxt=int(np.floor((phases[state]+advance+np.pi)*32/(2*np.pi)))%32
            # Quantized phase increment, with the branch chosen by prediction.
            quantized=J.wrap(phases[nxt]-phases[state])
            quantized+=2*np.pi*np.rint((advance-quantized)/(2*np.pi))
            hz=quantized*20e6/(2*np.pi)
            dac=int(np.rint(np.clip((hz+6e6)/15e6*63,0,63)))
            lut[state*32+token]=dac|(nxt<<6)
    for bank in range(4):
        for raw in range(256):
            i,q=raw>>4,raw&15
            z=(i-16 if i>=8 else i)+.5+1j*((q-16 if q>=8 else q)+.5)
            if abs(z)<.8:
                token=24+4*(bank%2)+int(np.floor((np.angle(z)+np.pi)*4/(2*np.pi)))%4
            else:
                token=int(np.floor((np.angle(z)+np.pi)*24/(2*np.pi)))%24
            lut[bank*256+raw]|=token<<11
    m.update(name=f'PC-ERASURE-p{low/1e6:g}-{high/1e6:g}-g{gain:g}',lut=lut.tolist())
    m.pop('formula',None)
    m['research']=dict(mode_memory='instruction_path',priors_hz=[low,high],
        coarse_gain=gain,ordinary_angles=24,phase_states=32,
        switch='delayed_published_DAC_MSB',physical_acceptance=False,
        limitation='two_fixed_priors_only_origin_cells_not_a_full_second_order_PLL')
    return m


def source(m):
    return '# Exact coarse predictive update; C5VRX historical PC schedule.\n'+P.source(m)


def candidates():
    return [build(a,b,g) for a,b,g in ((0.,0.,.5),(-1e6,4e6,.5),
        (-3e6,4e6,.5),(-5e6,4e6,.5),(-3e6,4e6,.25))]


def verify():
    raw=np.random.default_rng(9100190).integers(0,256,8192,dtype=np.uint8)
    for m in candidates():
        stats={}
        actual=np.asarray(BS.simulate(source(m),raw,len(raw),stats=stats,wrap_rom=True))&63
        assert np.array_equal(actual,P.codes(raw,np.asarray(m['lut'],np.uint16)))
        assert all(pc%2==k%2 for k,pc in enumerate(stats['trace']))
        assert stats['bundles']==len(raw)-1
    return dict(models=5,bytes_per_stream=8192,slots=8,lut_bytes=2048,
                scope='independent_source_dataflow_not_C5_timing_or_quality',
                control='24_angle_zero_prior_not_byte_exact_U85Q')


if __name__=='__main__':
    import json
    print(json.dumps(verify()))
