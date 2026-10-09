"""C5VRX arithmetic phase-difference research, real shared LUT16 + counter A.

One rawA decode and one scalar DAC lookup per 50ns. Counter A performs
subtraction; phase need not occupy transition-table state bits. Output is
one DAC byte at 20 MHz, not [D,D] at 40 MHz: cannot run under old TX40 config.
This source is ALWAYS checked by the actual Espressif assembler, which
rejects counter/LUT32 multiplexing imagined by a permissive source model.
"""
import numpy as np
from numba import njit
import hardware as H


def source(m):
 s='''# C5VRX by Twotoz/contributors: arithmetic phase difference; REQUIRES TX20.
# RX40/raw32K, TX-only, no CPU sample DSP. Single shared LUT16/2KiB.
cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 16
lut '''+' '.join(map(str,m['lut']))+'\n'
 ctx=m.get('context',False)
 for k in range(4):
  s+=f'''decode_{k}:
    set 0..5 L0..L5,
    set 8..15 O8..O15,
    set 16..23 0..7,
    set 24 H,
    set 25 {'A7' if ctx else 'O1'},
    set 26..31 O2..O7,
    read 16,
    write 8,
    addctiah
risk_{k}:
    set 0..7 L0..L7,
    set 8..15 L8..L15,
    set 16..23 A8..A15,
    set 24 L,
'''
  s+=('    set 25 A7,\n    set 26..31 O10..O15,\n' if ctx else '    set 25..31 O9..O15,\n')
  s+='    '+('ldctia' if ctx else 'ldctiah')+'\n'
 return s


def synthesize(context=False,deviation=1.,centre=1e6):
 i,q=H.B.D.cells(np.arange(256));p=np.angle(i+.5+1j*(q+.5))%(2*np.pi)
 P=64 if context else 128
 phase=np.floor(p*P/(2*np.pi)+.5).astype(int)%P
 pos=(4*phase+1) if context else (2*phase+1)
 neg=((-4*phase)&255) if context else ((-pos-1)&255)
 words=np.zeros(1024,np.uint16)
 for bank in (1,3):words[bank*256:(bank+1)*256]=pos|(neg<<8)
 for bank in (0,2):
  d=np.arange(256);h=bank//2
  # Exact128 has a -1 encoded subtraction bias. Context64 is a
  # coarse prediction class, with counter-low carry explicitly simulated.
  step=((d+(1 if not context else -(1+4*h)))+128)%256-128
  hz=step*20e6/256
  nominal=1e6+(hz-centre)/deviation
  code=np.rint(np.clip((nominal*256/20e6-H.B.P.OFFSET)*H.B.P.SCALE,0,63)).astype(np.uint16)
  words[bank*256:(bank+1)*256]=code
 return dict(name='COUNTER DIFF '+('CONTEXT64' if context else 'EXACT128'),context=context,
             lut=words.astype(int).tolist(),output_hz=20000000,input_hz=40000000,
             deviation=deviation,centre=centre)


@njit(cache=True)
def codes(raw,lut,ctx):
 y=np.zeros(len(raw)//2,np.uint8);a=0;pos=neg=last_dac=0
 for k in range(len(y)):
  y[k]=last_dac
  hint=(a>>7)&1 if ctx else ((pos>>1)&1)
  encidx=int(raw[2*k])|256|(hint<<9)
  operand=(pos&252)|1|(hint<<1)
  high=((a>>8)+operand+(((a&255)+int(raw[2*k]))>>8))&255
  hh=(a>>7)&1 if ctx else ((neg>>1)&1)
  idx=high|(hh<<9)
  last_dac=int(lut[idx])&63
  value=int(lut[encidx]);pos=value&255
  loaded=(neg&252)|(hh<<1)
  a=(loaded<<8)|(high if ctx else (a&255))
  neg=(value>>8)&255
 return y


def decode(raw,m):
 c=codes(raw,np.asarray(m['lut'],np.uint16),m.get('context',False))
 return H.B.D.goggle(H.B.DAC_VOLTS[np.repeat(c,2)])
