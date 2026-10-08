"""C5VRX shared-word decoder/tracker synthesis; LUT16, two bundles.

Transition DAC6 + state occupy low bits. Decoder tokens occupy independent
high bits of the SAME 256 LUT words at addresses768..1023. All 1024 entries
can therefore contain transitions. This is a source model, not board proof.
"""
import numpy as np
from numba import njit
import hardware as H
import compile_overlay as C


def cost(token_bits,context_bits=0,counter_phase=False,pair_layout=None):
    return C.cost(token_bits,context_bits,counter_phase,pair_layout)


def pair_cells(layout):
    """Mid-cell signed IQ values of both samples for each PAIR address."""
    bits=C.pair_bits(layout);address=np.arange(1024);word=np.zeros(1024,np.int64)
    for j,bit in enumerate(bits):word|=((address>>j)&1)<<bit
    values=[]
    for keep,base in zip(map(int,layout),(4,0,12,8)):
        nibble=(word>>base)&15;signed=np.where(nibble>7,nibble-16,nibble)
        values.append(signed+(1<<(4-keep))/2)
    return values[0]+1j*values[1],values[2]+1j*values[3]


def synthesize(p):
    for key in ('token_bits','phases','confidence_groups'):
        value=p.get(key,1)
        if type(value) is not int:raise ValueError('integer state allocation required')
    b=int(p['token_bits']);context=p.get('context_bits',0)
    counter=p.get('counter_phase',False);layout=p.get('pair_layout')
    resource=cost(b,context,counter,layout);n=1<<b;states=resource['states']
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
    if layout:
        # Combine both samples with a fixed half-span carrier-prior rotation;
        # no state or CPU is involved. B may be weighted below A.
        if not 0<=p['pair_weight']<=1.5 or not abs(p['pair_rotation'])<=np.pi:
            raise ValueError('invalid pair combination')
        za,zb=pair_cells(layout)
        z=za+float(p['pair_weight'])*zb*np.exp(-1j*float(p['pair_rotation']))
        if 'observation_vectors' in p:
            # Learned offline: mean unit carrier phasor of the clean received
            # signal at sample A, conditioned on each 10-bit address. Its
            # angle is the observation and its length the reliability. Never
            # seen addresses keep the weak geometric pair estimate.
            learned=np.asarray(p['observation_vectors'],float)
            if learned.shape!=(1024,2) or not np.isfinite(learned).all() or np.any(np.hypot(*learned.T)>1+1e-9):
                raise ValueError('observation vectors need 1024 finite unit-disc entries')
            learned=learned[:,0]+1j*learned[:,1]
            z=np.where(abs(learned)>0,learned,.01*z/np.maximum(abs(z),1e-12))
    radius=abs(z);rotation=float(p['rotation'])
    angle=np.floor((np.angle(z)-rotation)*obs/(2*np.pi)+.5).astype(int)%obs
    if groups==1:confidence=np.zeros(len(z),int)
    else:confidence=np.minimum((radius/float(p['radius_scale'])*groups).astype(int),groups-1)
    encoder=angle+obs*confidence
    occupancy=np.bincount(encoder,minlength=n)
    rmean=np.divide(np.bincount(encoder,weights=radius,minlength=n),occupancy,
                    out=np.ones(n),where=occupancy>0)
    reliability=np.clip(rmean/float(p['radius_scale']),float(p['confidence_floor']),1)[None,:]
    # Noise shrinks the mean phase-detector output (Bussgang); a first-order
    # loop passes that straight into smaller video and shallower sync. With
    # `unbias` > 0 the correction is divided by the token's mean reliability
    # (learned phasor length or radius), restoring its expected size.
    unbias=float(p.get('unbias',0.))
    if not 0<=unbias<=1:raise ValueError('unbias outside 0..1')
    expansion=np.clip(rmean/float(p['radius_scale']),.2,1)[None,:]**(-unbias)
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
    innovation=innovation*expansion
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
    start=0 if context or counter or layout else 768
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


@njit(cache=True)
def pair_codes(raw,lut,b,bits):
    out=np.zeros(len(raw),np.uint8);state=0;mask=(1<<(10-b))-1
    for k in range(len(raw)//2-1):
        word=np.int64(raw[2*k])|(np.int64(raw[2*k+1])<<8);address=0
        for j in range(10):address|=((word>>bits[j])&1)<<j
        token=np.int64(lut[address])>>(16-b)
        value=np.int64(lut[(state<<b)+token]);state=(value>>6)&mask
        out[2*k+2]=out[2*k+3]=value&63
    return out


@njit(cache=True)
def pair_indices(raw,lut,b,bits):
    result=np.zeros(len(raw)//2-1,np.int64);state=0;mask=(1<<(10-b))-1
    for k in range(len(result)):
        word=np.int64(raw[2*k])|(np.int64(raw[2*k+1])<<8);address=0
        for j in range(10):address|=((word>>bits[j])&1)<<j
        token=np.int64(lut[address])>>(16-b)
        address=(state<<b)+token;result[k]=address;state=(np.int64(lut[address])>>6)&mask
    return result


def model_codes(raw,m,lut=None):
    p=m['params'];lut=np.array(m['lut'],np.uint16) if lut is None else lut
    if p.get('pair_layout'):return pair_codes(raw,lut,p['token_bits'],np.array(C.pair_bits(p['pair_layout']),np.int64))
    return codes(raw,lut,p['token_bits'],p.get('context_bits',0),p.get('counter_phase',False))


def model_indices(raw,m,lut=None):
    p=m['params'];lut=np.array(m['lut'],np.uint16) if lut is None else lut
    if p.get('pair_layout'):return pair_indices(raw,lut,p['token_bits'],np.array(C.pair_bits(p['pair_layout']),np.int64))
    return indices(raw,lut,p['token_bits'],p.get('context_bits',0),p.get('counter_phase',False))


def decode(raw,m):
    return H.B.D.goggle(H.B.DAC_VOLTS[model_codes(raw,m)])


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
