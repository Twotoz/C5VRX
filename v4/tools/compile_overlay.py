"""Standard-library C5VRX LUT16 shared decoder/state compiler."""
from bs_model import parse


# PAIR decoders address the shared LUT with kept most-significant bits of
# both raw IQ40 samples in a 50ns span: layout digits are kept bits for
# (I_A,Q_A,I_B,Q_B), sign first. Input bits: Q_A0..3, I_A4..7, Q_B8..11,
# I_B12..15. The second sample is otherwise discarded by plain schedules.
def pair_bits(layout):
    if (type(layout) is not str or len(layout)!=4 or not layout.isdigit() or
            any(not 1<=int(k)<=4 for k in layout) or sum(map(int,layout))!=10):
        raise ValueError('pair layout needs four 1..4 kept-bit counts totalling ten')
    bits=[]
    for keep,base in zip(map(int,layout),(4,0,12,8)):
        bits+=list(range(base+4-keep,base+4))
    return bits


def cost(token_bits,context_bits=0,counter_phase=False,pair_layout=None):
    if token_bits not in (2,3,4,5,6):raise ValueError('unsupported token width')
    if context_bits not in (0,2):raise ValueError('no compiled context schedule')
    if counter_phase and token_bits!=5:raise ValueError('only phase32 counter schedule is implemented')
    if pair_layout is not None:
        pair_bits(pair_layout)
        if context_bits or counter_phase:raise ValueError('pair address excludes context/counter schedules')
    state_bits=10-token_bits
    assert 6+state_bits+token_bits==16
    return dict(lut_bytes=2048,lut_bits=16,slots=8,bundles=2,lookups=2,
                token_bits=token_bits,state_bits=state_bits,states=1<<state_bits,
                span_ns=50,rx_hz=40000000,unique_hz=20000000,dac_hz=40000000,
                raw_ring=32768,cpu_samples=False,context_bits=context_bits,
                counter_bits=16 if counter_phase else 0,pair_layout=pair_layout)


def build(m):
    b=int(m['params']['token_bits']);context=m['params'].get('context_bits',0)
    state_context=m['params'].get('context_state_bits',False)
    history=m['params'].get('history_observation',False)
    if history and (context or m['params'].get('counter_phase') or m['params'].get('pair_layout')):
        raise ValueError('history register schedule is exclusive of other encoders')
    if state_context and (context!=2 or b not in (2,3,4)):
        raise ValueError('state-conditioned encoder needs two context bits and >=64 states')
    counter=m['params'].get('counter_phase',False);layout=m['params'].get('pair_layout')
    r=cost(b,context,counter,layout);sb=r['state_bits'];lut=m['lut']
    if len(lut)!=1024 or any(type(v)is not int or not 0<=v<65536 for v in lut):raise ValueError('LUT')
    s='''# C5VRX by Twotoz/contributors: shared-word LUT16 decoder/tracker.
# IQ40/raw32K/TX-only, unique20 duplicate DAC6 at40M; no CPU sample loop.
# Source feasibility is not board timing or picture/range acceptance.
cfg prefetch '''+('false' if history else 'true')+'''
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 16
lut '''+' '.join(map(str,lut))+'\n'
    for k in range(4):
        address=('set 24 15,'+chr(10)+'    set 25 11,') if context else ('set 24..25 L,' if counter else 'set 24..25 H,')
        if state_context:
            address=f'set 24 L{6+sb-2},'+chr(10)+f'    set 25 L{6+sb-1},'
        if history:
            # Current full IQ at32..39; previous A's I/Q signs at23/19.
            # REG_MEM1 low16 and LUT16 are simultaneously addressable on C5.
            keep=[36,37,38,39,32,33,34,35,23,19]
            address=(chr(10)+'    ').join(f'set {16+j} {bit},' for j,bit in enumerate(keep))
        if layout:
            address=(chr(10)+'    ').join(f'set {16+j} {bit},' for j,bit in enumerate(pair_bits(layout)))
        carry=('set 26 L,'+chr(10)+'    set 27..31 L6..L10,') if counter else 'set 26..31 L6..L11,'
        state_source='A11..A15' if counter else f'O26..O{25+min(6,sb)}'
        s+=f'''controller_{k}:
    set 0..5 L0..L5,
    set 8..13 L0..L5,
    set 14..15 L12..L13,
    {'' if layout or history else 'set 16..23 0..7,'+chr(10)+'    '}{address}
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
