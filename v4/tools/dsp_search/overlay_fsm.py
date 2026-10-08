"""C5VRX shared-word decoder/tracker synthesis; LUT16, two bundles.

Transition DAC6 + state occupy low bits. Decoder tokens occupy independent
high bits of the SAME 256 LUT words at addresses768..1023. All 1024 entries
can therefore contain transitions. This is a source model, not board proof.
"""
import numpy as np
from numba import njit
import hardware as H
import compile_overlay as C


def cost(token_bits,context_bits=0,counter_phase=False):
    return C.cost(token_bits,context_bits,counter_phase)


def synthesize(p):
    for key in ('token_bits','phases','confidence_groups'):
        value=p.get(key,1)
        if type(value) is not int:raise ValueError('integer state allocation required')
    b=int(p['token_bits']);context=p.get('context_bits',0)
    counter=p.get('counter_phase',False)
    resource=cost(b,context,counter);n=1<<b;states=resource['states']
    numeric=('kp','ki','output_gain','low_hz','high_hz','centre_hz','rotation','radius_scale',
             'confidence_floor','limit','adaptation')
    if any(not np.isfinite(p[k]) for k in numeric):raise ValueError('nonfinite parameter')
    if not (0<p['kp']<=2 and 0<p['ki']<=2 and 0<=p['output_gain']<=2 and
            0<=p['confidence_floor']<=1 and p['radius_scale']>0 and 0<p['limit']<=np.pi and
            0<=p['adaptation']<=2 and -10e6<p['low_hz']<p['high_hz']<10e6 and
            -10e6<p['centre_hz']<10e6):raise ValueError('unstable or out-of-band parameters')
    if p['error_function'] not in ('linear','clip','sine','tanh'):raise ValueError('unknown innovation')
    reconstruction=p.get('reconstruction','innovation')
    if reconstruction not in ('innovation','advance','quantized_advance','mixed_advance'):
        raise ValueError('unknown output reconstruction')
    if reconstruction=='mixed_advance' and not 0<=p['advance_mix']<=1:
        raise ValueError('invalid phase-advance blend')
    phases=int(p['phases']);groups=int(p.get('confidence_groups',1))
    if counter and (phases!=32 or not 0<=p['address_bias']<=1023):raise ValueError('counter model')
    if phases<4 or phases>states or phases&(phases-1):raise ValueError('phase grid')
    if groups not in (1,2,4) or n//groups<4:raise ValueError('observation grid')
    frequencies=states//phases;obs=n//groups
    i,q=H.B.D.cells(np.arange(1024 if context else 256)%256);z=i+.5+1j*(q+.5)
    if context:
        if not 0<=p['context_weight']<=1 or not 0<p['context_radius']<=8:
            raise ValueError('unstable context interpolation')
        quadrant=np.arange(1024)//256
        next_z=(1-2*(quadrant&1))+1j*(1-2*((quadrant>>1)&1))
        aligned=next_z*p['context_radius']/np.sqrt(2)*np.exp(-2j*np.pi*p['centre_hz']/40e6)
        z=(1-p['context_weight'])*z+p['context_weight']*aligned
    radius=abs(z);rotation=float(p['rotation'])
    angle=np.floor((np.angle(z)-rotation)*obs/(2*np.pi)+.5).astype(int)%obs
    if groups==1:confidence=np.zeros(len(z),int)
    else:confidence=np.minimum((radius/float(p['radius_scale'])*groups).astype(int),groups-1)
    encoder=angle+obs*confidence
    occupancy=np.bincount(encoder,minlength=n)
    rmean=np.divide(np.bincount(encoder,weights=radius,minlength=n),occupancy,
                    out=np.ones(n),where=occupancy>0)
    reliability=np.clip(rmean/float(p['radius_scale']),float(p['confidence_floor']),1)[None,:]
    if groups==1:reliability=np.ones((1,n))
    if frequencies==1:freq=np.array([float(p['centre_hz'])])
    else:freq=np.linspace(float(p['low_hz']),float(p['high_hz']),frequencies)
    omega=freq*2*np.pi/20e6
    index=np.arange(states);phase=(index%phases)*2*np.pi/phases
    old=omega[index//phases,None];predicted=phase[:,None]+(0 if counter else old)
    observed=(np.arange(n)%obs)[None,:]*2*np.pi/obs+rotation
    if p.get('centroid_observations'):
        real=np.bincount(encoder,weights=z.real,minlength=n)
        imag=np.bincount(encoder,weights=z.imag,minlength=n)
        observed=np.where(occupancy>0,np.arctan2(imag,real),observed.ravel())[None,:]
    error=(observed-predicted+np.pi)%(2*np.pi)-np.pi
    limit=float(p['limit']);mode=p['error_function']
    innovation=error if mode=='linear' else np.clip(error,-limit,limit) if mode=='clip' else np.sin(error) if mode=='sine' else limit*np.tanh(error/limit)
    gain=float(p['kp'])*reliability
    if p.get('adaptive'):
        gain=np.clip(gain*(1+float(p['adaptation'])*abs(error)/np.pi),0,1.8)
    corrected=old+float(p['ki'])*reliability*innovation
    next_f=np.argmin(abs(corrected[...,None]-omega),axis=-1)
    next_phase=np.floor((predicted+gain*innovation)*phases/(2*np.pi)+.5).astype(int)%phases
    next_state=next_f*phases+next_phase
    if counter:
        delta=(old+gain*innovation)*32/(2*np.pi)-p['address_bias']/2048
        next_phase=np.floor(delta+.5).astype(int)%32;next_state=next_phase
    # Blend a predictive frequency estimate and unattenuated differential
    # innovation. Strong reconstruction is evaluated with fixed OVP levels.
    blend=float(p['output_gain']);out=old+blend*error+(1-blend)*(corrected-old)
    if p.get('confidence_output'):out=old+reliability*(out-old)
    if reconstruction!='innovation':
        advance=old+gain*innovation
        quantized=(next_phase*2*np.pi/phases-phase[:,None]+np.pi)%(2*np.pi)-np.pi
        if counter:quantized=(next_phase*2*np.pi/32+np.pi)%(2*np.pi)-np.pi+p['address_bias']/2048*2*np.pi/32
        if reconstruction=='advance':out=advance
        elif reconstruction=='quantized_advance':out=quantized
        elif reconstruction=='mixed_advance':out=(1-float(p['advance_mix']))*advance+float(p['advance_mix'])*quantized
        else:raise ValueError('unknown output reconstruction')
    code=np.rint(np.clip((out*128/np.pi-H.B.P.OFFSET)*H.B.P.SCALE,0,63)).astype(np.uint16)
    lut=(code+(next_state.astype(np.uint16)<<6)).ravel()
    start=0 if context or counter else 768
    lut[start:start+len(encoder)]=lut[start:start+len(encoder)] | (encoder.astype(np.uint16)<<(16-b))
    return dict(name='OVERLAY',params=p,lut=lut.tolist(),cost=resource)


def compile_model(m):
    return C.build(m)


@njit(cache=True)
def codes(raw,lut,b,context=0,counter=False):
    out=np.zeros(len(raw),np.uint8);state=0;accumulator=0;mask=(1<<(10-b))-1
    for k in range(len(raw)//2-1):
        address=(0 if counter else 768)+raw[2*k]
        if context:
            q=raw[2*k+1];address=raw[2*k]+(((q>>7)&1)<<8)+(((q>>3)&1)<<9)
        token=np.int64(lut[address])>>(16-b)
        lookup_state=state
        if counter:
            accumulator=(accumulator+address+(state<<11))&65535
            lookup_state=accumulator>>11
        value=np.int64(lut[(lookup_state<<b)+token]);state=(value>>6)&mask
        out[2*k+2]=out[2*k+3]=value&63
    return out


def decode(raw,m):
    return H.B.D.goggle(H.B.DAC_VOLTS[codes(raw,np.array(m['lut'],np.uint16),m['params']['token_bits'],m['params'].get('context_bits',0),m['params'].get('counter_phase',False))])


@njit(cache=True)
def indices(raw,lut,b,context=0,counter=False):
    """Actual transition addresses, independent of the DAC6 reconstruction."""
    result=np.zeros(len(raw)//2-1,np.int64);state=0;accumulator=0;mask=(1<<(10-b))-1
    for k in range(len(result)):
        address=(0 if counter else 768)+raw[2*k]
        if context:
            q=raw[2*k+1];address=raw[2*k]+(((q>>7)&1)<<8)+(((q>>3)&1)<<9)
        token=np.int64(lut[address])>>(16-b)
        lookup_state=state
        if counter:
            accumulator=(accumulator+address+(state<<11))&65535;lookup_state=accumulator>>11
        address=(lookup_state<<b)+token;result[k]=address;state=(np.int64(lut[address])>>6)&mask
    return result


def propose(rng,extended=False):
    b=int(rng.choice([2,3,4,5,6]));sb=10-b
    phases=int(2**rng.integers(2,sb+1));groups=int(rng.choice([1,1,1,2,4]))
    if (1<<b)//groups<4:groups=1
    p=dict(token_bits=b,phases=phases,confidence_groups=groups,
                kp=float(rng.uniform(.1,1.5)),ki=float(10**rng.uniform(-2,.25)),
                low_hz=float(rng.uniform(-5e6,-2.35e6)),high_hz=float(rng.uniform(4.35e6,8e6)),
                centre_hz=float(rng.uniform(-.5e6,2e6)),rotation=float(rng.uniform(0,2*np.pi/(1<<b))),
                radius_scale=float(rng.uniform(1.2,7)),confidence_floor=float(rng.uniform(0,.9)),
                error_function=str(rng.choice(['linear','linear','clip','sine','tanh'])),limit=float(rng.uniform(.15,np.pi)),
                adaptive=bool(rng.integers(2)),adaptation=float(rng.uniform(0,2)),
                output_gain=float(rng.uniform(.1,1.3)),confidence_output=bool(rng.integers(2)))
    if extended:
        p.update(centroid_observations=bool(rng.integers(2)),
                 reconstruction=str(rng.choice(['innovation','advance','quantized_advance','mixed_advance'])),
                 advance_mix=float(rng.random()))
    return p


def test():
    rng=np.random.default_rng(12001);raw=rng.integers(0,256,4096,dtype=np.uint8)
    for b in range(2,7):
        p=propose(rng);p.update(token_bits=b,phases=4,confidence_groups=1)
        m=synthesize(p);source=compile_model(m)
        actual=H.BS.simulate(source,raw,len(raw),wrap_rom=True)
        assert np.array_equal(np.array(actual)&63,codes(raw,np.array(m['lut'],np.uint16),b)),b
    print('PASS overlay: every token/state allocation matches independent source execution')


if __name__=='__main__':test()
