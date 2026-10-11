"""C5VRX/Twotoz two-counter coherent-product schedule, local research.

Weighted correlation is established prior work (MEGA_DEMOD_STUDY). This
experiment tests a concrete two-bundle mapping and its unavoidable address /
counter-operand coupling. Split512 product +512 angle tables, no LUT sharing
fiction. Cyclic growing windows, not a rolling boxcar or leak-free IIR.

REJECTED by official C5 assembler: LUT16 masks counter B source bits in the
worker. Host dataflow emulation does not model that physical source conflict.
Do not treat the following sources or their host outputs as C5-executable.
"""
import json
import numpy as np
from numba import njit
import bs_model as BS
import local_joint_observation as J
import overlay_fsm as O

LAYOUT=(3,2,2,2)
BITS=tuple(bit for keep,base in zip(LAYOUT,(4,0,12,8))
      for bit in range(base+4-keep,base+4))


def component(word,base,keep):
    nib=(word>>base)&15
    signed=nib-16 if nib>=8 else nib
    quantum=1<<(4-keep)
    return (signed//quantum+.5)*quantum


def build(window=2,scale=4.):
    if window not in (1,2,4):raise ValueError(window)
    lut=np.zeros(1024,np.int64)
    for address in range(512):
        word=sum(((address>>j)&1)<<bit for j,bit in enumerate(BITS))
        za=complex(component(word,4,3),component(word,0,2))
        zb=complex(component(word,12,2),component(word,8,2))
        prod=zb*za.conjugate()
        # Expected arithmetic nuisance only, not true frequency/CNR. Exact
        # nuisance remains in codes() and the independent assembly emulator.
        i=int(np.clip(np.rint(prod.real/scale-.75),-32,31))&63
        q=int(np.clip(np.rint(prod.imag/scale-.25),-32,31))&63
        lut[address]=i|(q<<6)
    for i in range(16):
        for q in range(32):
            real=(i-16 if i>=8 else i)*4+2
            imag=(q-32 if q>=16 else q)*2+1
            hz=np.angle(complex(real,imag))*40e6/(2*np.pi)
            lut[512+i+16*q]=int(np.rint(np.clip((hz+6e6)/15e6*63,0,63)))
    return dict(name=f'COMPLEX-CTR-N{window}-scale{scale:g}',
        params=dict(window=window,scale=scale),lut=lut.tolist(),
        research=dict(complex_counter=True,layout=list(LAYOUT),
            windows='cyclic_lengths_1_to_N_not_rolling',physical_acceptance=False,
            coupling='B_adds_encoder_address_A_adds_decoder_address'))


def source(m):
    N=m['params']['window']
    s='# C5VRX/Twotoz/contributors; coherent counter research only.\n'
    s+='cfg prefetch true\ncfg eof_on downstream\ncfg trailing_bytes 0\ncfg lut_width_bits 16\n'
    s+='lut '+' '.join(map(str,m['lut']))+'\n'
    for k in range(4):
        address='\n    '.join(f'set {16+j} {bit},' for j,bit in enumerate(BITS))
        s+=f'''controller_{k}:
    set 0..5 L0..L5,
    set 8..13 L0..L5,
    {address}
    set 25 L,
    set 26..31 O0..O5,
    read 16,
    write 16,
    {'ldctib' if k%N==1%N else 'addctib'}
worker_{k}:
    set 0..5 L6..L11,
    set 16..19 A12..A15,
    set 20..24 B11..B15,
    set 25 H,
    set 26..31 L0..L5,
    {'ldctia' if k%N==0 else 'addctia'}
'''
    _,lut,blocks,_=BS.parse(s)
    assert len(lut)==1024 and len(blocks)==8
    return s


@njit(cache=True)
def codes(raw,lut,N):
    out=np.zeros(len(raw),np.uint8)
    A=B=previous_q=previous_dac=0
    for k in range(len(raw)//2):
        out[2*k]=out[2*k+1]=previous_dac
        word=int(raw[2*k])|(int(raw[2*k+1])<<8)
        address=0
        for j in range(9):address|=((word>>BITS[j])&1)<<j
        operand=address|(previous_q<<10)
        B=(operand if k%N==1%N else B+operand)&65535
        decoder=512|((A>>12)&15)|(((B>>11)&31)<<4)
        prod=int(lut[address])
        previous_q=(prod>>6)&63
        operand=decoder|((prod&63)<<10)
        A=(operand if k%N==0 else A+operand)&65535
        previous_dac=int(lut[decoder])&63
    return out


def decode(raw,m):
    return O.H.B.D.goggle(O.H.B.DAC_VOLTS[codes(raw,
                  np.asarray(m['lut'],np.uint16),m['params']['window'])])


def candidates():return [build(N) for N in (1,2,4)]


def verify():
    from functools import lru_cache
    old=BS.parse;BS.parse=lru_cache(maxsize=3)(old)
    try:
        raw=np.random.default_rng(7200190).integers(0,256,8192,dtype=np.uint8)
        for m in candidates():
            actual=np.asarray(BS.simulate(source(m),raw,len(raw),wrap_rom=True))&63
            expected=codes(raw,np.asarray(m['lut'],np.uint16),m['params']['window'])
            assert np.array_equal(actual,expected),(m['name'],np.flatnonzero(actual!=expected)[:8])
        return dict(models=3,bytes_per_stream=8192,lut_bytes=2048,slots=8,
            status='source_dataflow_only_not_assembler_or_physical_acceptance')
    finally:BS.parse=old


if __name__=='__main__':print(json.dumps(verify()))
