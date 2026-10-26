"""C5VRX by Twotoz/contributors: U80P32-D7, a seven-bit-DAC tracker table.

Adapts the U80P64-R1 closed-form tracker builder (research/p64_model.py, PR
#250 repair) to the seven-branch resistor DAC with D10 as the new LSB
(README "Reserved extra branch", docs/FINDINGS.md GPIO27 entry). The shared
LUT16 word cannot hold 64 states, 16 tokens and seven DAC bits, so this
variant keeps 16 tokens and 32 phase states:

    bits 0..6   physical DAC lanes: lane k = D4..D9 (k=0..5), lane 6 = D10
    bits 7..11  next state (32 states: 7 coarse sync + 25 video)
    bits 12..15 observation token of the pair (same PAIR4411 address)

Logical code c7 (0..127) has bit 0 on D10 and bits 1..6 on D4..D9, so lanes
0..5 hold c7 >> 1 and lane 6 holds c7 & 1. Menu/idle bytes keep their old
meaning (lane 6 low = c7 even = the old six-bit level).

As in U80P64-R1 the emitted value is the committed state advance (exact on
average; `output_mix` > 0 blends in the uncommitted prediction, which the
search rejected for its transfer bias). The code is chosen by inverting the
nominal loaded seven-branch network (15k/8.2k/3.9k/2k/1k/470R/240R into
200R) against the six-bit linear span, so equal frequency steps give equal
node-voltage steps on the seven-bit board and the old black/white levels.
With 25 video states a video-to-video advance moves in 14.4-degree steps
(0.8 MHz, ~6.7 seven-bit codes): the seventh bit halves the level placement
error of each step; it does not make the tracker finer than P64.

Host model only: no board, goggle, PAL/NTSC or range result is claimed.
"""
import hashlib
import json
import math
import re
import struct
import sys
from pathlib import Path

import numpy as np
from numba import njit

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = 'U80P32-D7'
PARAMS = ROOT / 'research/data/p32d7.json'
HEADER = ROOT / 'main/p32d7_tables.h'
PROGRAM = ROOT / 'main/p32d7.bsasm'

# Physical lanes in PARLIO TX order (bit k of the output byte -> pin).
LANE_OHMS = (8200.0, 3900.0, 2000.0, 1000.0, 470.0, 240.0, 15000.0)
LANE_PINS = ('D4', 'D5', 'D6', 'D7', 'D8', 'D9', 'D10')
LOAD_OHMS = 200.0
SIX_OHMS = LANE_OHMS[:6]
LO_HZ, HI_HZ = -6.0e6, 9.0e6         # P64/U85Q video window per 50 ns pair


def lanes_of_code(c7):
    return ((c7 >> 1) & 63) | ((c7 & 1) << 6)


def code_of_lanes(lanes):
    return ((lanes & 63) << 1) | ((lanes >> 6) & 1)


def node_volts(lanes, ohms=LANE_OHMS, vdd=3.3):
    """Nominal loaded node voltage; pin driver resistance ignored."""
    g = [1.0 / r for r in ohms]
    on = sum(gk for k, gk in enumerate(g) if lanes & (1 << k))
    return vdd * on / (sum(g) + 1.0 / LOAD_OHMS)


LANE_VOLTS = np.array([node_volts(v) for v in range(128)])
SIX_VOLTS = np.array([node_volts(v, SIX_OHMS) for v in range(64)])
# Code-indexed seven-bit voltages, and the six-bit linear span they target.
CODE_VOLTS = np.array([LANE_VOLTS[lanes_of_code(c)] for c in range(128)])
SIX_FULL = SIX_VOLTS[63]


def code_for_fraction(x):
    """Seven-bit code whose nominal voltage is nearest x * six-bit full scale
    (x = 0 sync tip of the window .. 1 top of the window)."""
    target = min(1.0, max(0.0, x)) * SIX_FULL
    return int(np.argmin(np.abs(CODE_VOLTS - target)))


def _nibble(word, positions):
    value = sum(((word >> position) & 1) << bit for bit, position in enumerate(positions))
    return (value - 16 if value > 7 else value) + 0.5


def layout(params):
    """(token bits, state bits): token + state + seven DAC bits fill 16."""
    token_bits = int(params.get('token_bits', 4))
    return token_bits, 9 - token_bits


def build_table(params):
    token_bits, state_bits = layout(params)
    n_states = 1 << state_bits
    n_sync = int(params['n_sync'])
    n_vid = n_states - n_sync
    tokens = 1 << token_bits
    bits = (4, 5, 6, 7, 0, 1, 2, 3, 15, 11)
    lo_step = 2 * math.pi * LO_HZ * 50e-9
    hi_step = 2 * math.pi * HI_HZ * 50e-9

    tok = np.zeros(1024, dtype=np.int64)
    for a in range(1024):
        word = 0
        for j in range(10):
            word |= ((a >> j) & 1) << bits[j]
        ia, qa = _nibble(word, (4, 5, 6, 7)), _nibble(word, (0, 1, 2, 3))
        tok[a] = int(math.floor((math.atan2(qa, ia) + math.pi) * tokens / (2 * math.pi))) % tokens
    obs = (np.arange(tokens) + 0.5) * 2 * math.pi / tokens - math.pi

    ph_sync = (np.arange(n_sync) + 0.5) * 2 * math.pi / n_sync - math.pi
    ph_vid = (np.arange(n_vid) + 0.5) * 2 * math.pi / n_vid - math.pi
    kp_sync, kp_vid = params['kp_sync'], params['kp_vid']
    v_sync, v_vid = params['v_sync_prior'], params['v_video_prior']
    exit_th, enter_th = params['exit_sync_th'], params['enter_sync_th']
    mix = params['output_mix']        # 0 committed advance .. 1 tracker d_theta
    lut = np.zeros(1024, dtype=np.int64)
    for s in range(n_states):
        is_sync = s < n_sync
        phase = ph_sync[s] if is_sync else ph_vid[s - n_sync]
        for t in range(tokens):
            err = (obs[t] - phase + math.pi) % (2 * math.pi) - math.pi
            if is_sync:
                d_theta = kp_sync * err + (1 - kp_sync) * v_sync
                video = d_theta > exit_th
            else:
                d_theta = kp_vid * err + (1 - kp_vid) * v_vid
                video = not d_theta < enter_th
            d_theta = max(lo_step, min(hi_step, d_theta))
            nxt_phase = (phase + d_theta + math.pi) % (2 * math.pi) - math.pi
            if video:
                nxt = n_sync + int(math.floor((nxt_phase + math.pi) * n_vid / (2 * math.pi))) % n_vid
            else:
                nxt = int(math.floor((nxt_phase + math.pi) * n_sync / (2 * math.pi))) % n_sync
            committed = ph_sync[nxt] if nxt < n_sync else ph_vid[nxt - n_sync]
            advance = (committed - phase + math.pi) % (2 * math.pi) - math.pi
            value = (1 - mix) * advance + mix * d_theta
            code = code_for_fraction((value - lo_step) / (hi_step - lo_step))
            entry = lanes_of_code(code) | (nxt << 7)
            # State lookups ignore address bit 9 (worker drives it low); fill
            # both halves so the table does not depend on that choice.
            lut[(s << token_bits) | t] = entry
            lut[512 + ((s << token_bits) | t)] = entry
    lut |= tok << (16 - token_bits)
    return lut.astype(np.uint16)


@njit(cache=True)
def pair_lanes(raw, lut, token_bits=4):
    """Two LUT16 lookups per pair, as main/p32d7.bsasm; lane bytes out."""
    out = np.zeros(len(raw), np.uint8)
    bits = (4, 5, 6, 7, 0, 1, 2, 3, 15, 11)
    state = 0
    for k in range(len(raw) // 2 - 1):
        word = np.int64(raw[2 * k]) | (np.int64(raw[2 * k + 1]) << 8)
        address = 0
        for j in range(10):
            address |= ((word >> bits[j]) & 1) << j
        token = np.int64(lut[address]) >> (16 - token_bits)
        value = np.int64(lut[(state << token_bits) + token])
        state = (value >> 7) & ((1 << (9 - token_bits)) - 1)
        out[2 * k + 2] = value & 127
        out[2 * k + 3] = value & 127
    return out


def codes_of(raw, lut):
    lanes = pair_lanes(raw, lut)
    return ((lanes & 63).astype(np.int64) << 1) | ((lanes >> 6) & 1)


def table():
    return build_table(json.loads(PARAMS.read_text())['params'])


def digest(lut):
    return hashlib.sha256(struct.pack('<1024H', *(int(v) for v in lut))).hexdigest()


def checked_in_table():
    text = HEADER.read_text()
    values = text.split('p32d7_table[1024] = {', 1)[1].split('}', 1)[0]
    return np.array([int(v, 16) for v in re.findall(r'0x[0-9a-fA-F]+', values)], dtype=np.uint16)


def render_header(lut):
    rows = [', '.join(f'0x{int(v):04x}' for v in lut[k:k + 12]) for k in range(0, 1024, 12)]
    body = ',\n    '.join(rows)
    return f'''/* C5VRX by Twotoz/contributors: {MODEL_ID} seven-bit DAC tracker table.
 * Generated by research/p32d7_model.py from research/data/p32d7.json.
 * 7 coarse / 25 video phase states, 16 tokens; bits 0..6 DAC lanes
 * (D4..D9, D10 = logical LSB), 7..11 next state, 12..15 token.
 * Host model only; seven-bit board test pending.
 * docs/FINDINGS.md "Seven-bit DAC test".
 */
#pragma once
#include <stdint.h>
#define P32D7_MODEL_ID "{MODEL_ID}"
#define P32D7_TABLE_ENTRIES 1024
#define P32D7_TABLE_SHA256 "{digest(lut)}"
static const uint16_t p32d7_table[1024] = {{
    {body}
}};
'''


def render_program(lut):
    words = ' '.join(str(int(v)) for v in lut)
    bundle = '''controller_{n}:
    set 0..6 L0..L6,
    set 8..14 L0..L6,
    set 16 4,
    set 17 5,
    set 18 6,
    set 19 7,
    set 20 0,
    set 21 1,
    set 22 2,
    set 23 3,
    set 24 15,
    set 25 11,
    set 27..31 L7..L11,
    read 16,
    write 16,
    nop
worker_{n}:
    set 16..19 L12..L15,
    set 20..24 O27..O31,
    nop
'''
    return (f'# C5VRX by Twotoz/contributors: {MODEL_ID} 32-state shared-word LUT16 tracker for the\n'
            '# seven-bit DAC. Bits 0..6 DAC lanes (lane 6 = D10 = logical LSB), 7..11 next state\n'
            '# (O27..O31), 12..15 token. Generated by research/p32d7_model.py; do not edit.\n'
            'cfg prefetch true\ncfg eof_on downstream\ncfg trailing_bytes 0\ncfg lut_width_bits 16\n'
            f'lut {words}\n' + ''.join(bundle.format(n=n) for n in range(4)))


if __name__ == '__main__':
    lut = table()
    if sys.argv[1:] == ['write']:
        HEADER.write_text(render_header(lut))
        PROGRAM.write_text(render_program(lut))
        print('wrote', HEADER.name, PROGRAM.name, digest(lut))
    else:
        assert np.array_equal(lut, checked_in_table()), 'p32d7_tables.h differs from research/data/p32d7.json'
        assert PROGRAM.read_text() == render_program(lut), 'p32d7.bsasm differs from the generator'
        print(f'PASS: {MODEL_ID} header and program reproduce from parameters ({digest(lut)})')
