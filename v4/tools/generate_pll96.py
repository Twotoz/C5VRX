#!/usr/bin/env python3
"""Quantized second-order PLL experiment for C5VRX by Twotoz/contributors.

Extends the existing C5VRX tracking-demod research. LUT16: 768 transitions
plus 256 raw decodes, 2048 bytes. 96 joint phase/frequency states.
This coarse pair20 PLL is NOT the floating IQ40 theory-benchmark winner.
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def wrap(x):
    return (x+math.pi) % (2*math.pi)-math.pi


def tables(p):
    kp, ki, direct, low, high, rotation = p[:6]
    phases = int(p[6]) if len(p) > 6 else 16
    assert phases in (2, 4, 8, 16, 32)
    frequencies = 96//phases
    assert 0 < kp <= 2 and 0 < ki <= 2 and 0 <= direct <= 2 and low < high
    freq = [2*math.pi*(low+(high-low)*j/(frequencies-1))/20e6 for j in range(frequencies)]
    encoder = []
    for raw in range(256):
        i, q = raw >> 4, raw & 15
        i = i-16 if i > 7 else i
        q = q-16 if q > 7 else q
        a = math.atan2(q+.5, i+.5)
        encoder.append(int(math.floor((a-rotation)*8/(2*math.pi)+.5)) % 8)
    transitions = []
    # Same nominal Phase50 voltage transfer as the existing research model.
    # Keep these pinned constants checked against optimize_pair in the study.
    offset, scale = -46.08, 63/117.76
    for state in range(96):
        phase, f = (state % phases)*2*math.pi/phases, freq[state//phases]
        predicted = phase+f
        for token in range(8):
            observed = token*2*math.pi/8+rotation
            error = wrap(observed-predicted)
            corrected = max(freq[0], min(freq[-1], f+ki*error))
            next_f = min(range(frequencies), key=lambda j: abs(freq[j]-corrected))
            next_phase = int(math.floor((predicted+kp*error)*phases/(2*math.pi)+.5)) % phases
            next_state = next_f*phases+next_phase
            out_freq = corrected+direct*kp*error
            code = round(max(0, min(63, (out_freq*128/math.pi-offset)*scale)))
            transitions.append(code | next_state << 6)
    return transitions, encoder


def build(p):
    transitions, encoder = tables(p)
    s = '''# C5VRX by Twotoz/contributors: experimental quantized PLL96.
# True recursive phase/frequency state; not the floating IQ40 PLL winner.
# raw Q4/I4 IQ40/raw32K/TX-only; two bundles/50ns, unique20, DAC6 [D,D].
# LUT16/2048 bytes, eight slots. Physical throughput/picture acceptance pending.
cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 16
lut '''+' '.join(map(str, transitions+encoder))+'\n'
    for k in range(4):
        s += f'''
controller_{k}:
    set 0..5 L0..L5,
    set 8..13 L0..L5,
    set 14 L12,
    set 16..23 0..7,
    set 24..25 H,
    set 26..31 L6..L11,
    read 16,
    write 16,
    nop
worker_{k}:
    set 16..18 L0..L2,
    set 19..24 O26..O31,
    set 25 O14,
    nop
'''
    return s


def generate():
    model = json.loads((ROOT/'tools/pll96_model.json').read_text())
    (ROOT/'firmware/programs/c5vrx4_pll96.bsasm').write_text(build(model['params']), encoding='utf-8')
    print('Generated PLL96: pinned phase/frequency PLL, LUT16/2KiB, pair20')


if __name__ == '__main__':
    generate()
