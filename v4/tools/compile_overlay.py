"""Standard-library C5VRX LUT16 shared decoder/state compiler."""
from bs_model import parse


def cost(token_bits,context_bits=0,counter_phase=False):
    if token_bits not in (2,3,4,5,6):raise ValueError('unsupported token width')
    if context_bits not in (0,2):raise ValueError('no compiled context schedule')
    if counter_phase and token_bits!=5:raise ValueError('only phase32 counter schedule is implemented')
    state_bits=10-token_bits
    assert 6+state_bits+token_bits==16
    return dict(lut_bytes=2048,lut_bits=16,slots=8,bundles=2,lookups=2,
                token_bits=token_bits,state_bits=state_bits,states=1<<state_bits,
                span_ns=50,rx_hz=40000000,unique_hz=20000000,dac_hz=40000000,
                raw_ring=32768,cpu_samples=False,context_bits=context_bits,
                counter_bits=16 if counter_phase else 0)


def build(m):
    b=int(m['params']['token_bits']);context=m['params'].get('context_bits',0)
    counter=m['params'].get('counter_phase',False)
    r=cost(b,context,counter);sb=r['state_bits'];lut=m['lut']
    if len(lut)!=1024 or any(type(v)is not int or not 0<=v<65536 for v in lut):raise ValueError('LUT')
    s='''# C5VRX by Twotoz/contributors: shared-word LUT16 decoder/tracker.
# IQ40/raw32K/TX-only, unique20 duplicate DAC6 at40M; no CPU sample loop.
# Source feasibility is not board timing or picture/range acceptance.
cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 16
lut '''+' '.join(map(str,lut))+'\n'
    for k in range(4):
        address=('set 24 15,'+chr(10)+'    set 25 11,') if context else ('set 24..25 L,' if counter else 'set 24..25 H,')
        carry=('set 26 L,'+chr(10)+'    set 27..31 L6..L10,') if counter else 'set 26..31 L6..L11,'
        state_source='A11..A15' if counter else f'O26..O{25+min(6,sb)}'
        s+=f'''controller_{k}:
    set 0..5 L0..L5,
    set 8..13 L0..L5,
    set 14..15 L12..L13,
    set 16..23 0..7,
    {address}
    {carry}
    read 16,
    write 16,
    {'addctia' if counter else 'nop'}
worker_{k}:
    set 16..{15+b} L{16-b}..L15,
    set {16+b}..{15+b+min(6,sb)} {state_source},
'''
        if sb>6:s+=f'    set {22+b}..25 O14..O{7+sb},\n'
        s+='    nop\n'
    cfg,_,blocks,_=parse(s)
    assert len(blocks)==8 and cfg['lut_width_bits']=='16'
    return s
