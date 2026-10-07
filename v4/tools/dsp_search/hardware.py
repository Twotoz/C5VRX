"""C5VRX by Twotoz/contributors: cost gates for implemented TX schedules.

This proves membership in supported templates, not all possible C5 programs.
Unknown schedules are rejected, never optimistically costed.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'detector_study'))
import broad_search as B
import compile_pair_demod as C
import bs_model as BS


def cost(m):
    if m.get('history_samples',2)>2:
        raise ValueError('no compiled schedule for this history depth')
    if m.get('tracking'):
        C.validate(m)
        if m.get('state_count',4)!=4:
            raise ValueError('LUT8 DAC6 transition carries exactly two state bits')
    else:
        B.compile_model(m)  # Validate actual address wires/table bounds first.
    return dict(lut_bytes=2048,lut_bits=8,slots=8,bundles_per_pair=2,
                lookups_per_pair=2,span_ns=50,state_bits=2 if m.get('tracking') else 0,
                retained_previous_bits=(m['current_tokens']-1).bit_length()-m['previous_shift'],
                raw_iq_bits=8,rx_hz=40000000,unique_dac_hz=20000000,dac_hz=40000000,
                ring_bytes=32768,cpu_samples=False,rx_scrambler=False,
                schedule='state4' if m.get('tracking') else 'pair_context')


def compile_model(m):
    cost(m)
    source=C.build(m) if m.get('tracking') else B.compile_model(m)
    cfg,lut,blocks,_=BS.parse(source)
    if len(lut)!=2048 or len(blocks)!=8 or cfg['lut_width_bits']!='8':
        raise ValueError('compiled resources disagree with cost model')
    return source


def pll_resolution(resolution_hz,span_hz=11_531_537,phases=8,tokens=8):
    # Absolute-frequency storage in the specific PLL96 LUT16 layout. Does
    # not exclude residue/dither/relative representations in other schedules.
    import math
    frequencies=math.ceil(span_hz/resolution_hz)+1
    states=frequencies*phases
    bytes_needed=2*(256+states*tokens)
    return dict(target_hz=resolution_hz,frequencies=frequencies,phase_states=phases,
                bytes_needed=bytes_needed,fits=bytes_needed<=2048,
                scope='absolute-frequency PLL96 layout only; not a universal impossibility proof')
