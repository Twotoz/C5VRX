"""C5VRX by Twotoz/contributors: U80P64-D10, U80P64-R1 with a seventh DAC branch.

Extends the U80P64-R1 table (research/p64_model.py, PR #250 repair) to the
seven-branch resistor DAC: D10/GPIO10 through 15 kOhm (R8), half the weight
of D4 (README "Reserved extra branch"). The tracker is unchanged: the same 64
phase states, 16 tokens, two LUT16 lookups per pair, DAC20 at TX40.

The LUT word has no free bit, so D10 reuses one: in the second TX40 byte of
each pair (byte B) lane 6 is the low bit of the next-state label (L6). Byte A
keeps lane 6 low. The goggle averages the pair, so D10 adds about a quarter
of a six-bit step whenever the next state has an odd label. This generator:

  1. relabels the 64 states (a permutation; behaviour is unchanged), chosen
     so that the label bit fits the level each transition needs, and
  2. picks each six-bit code so the pair-mean nominal node voltage is
     nearest U80P64-R1's unrounded committed-advance level.

DAC steps get finer; the tracker's 7.2-degree phase grid does not. Host
model: about +0.2..0.3 dB SINAD on strong signals, none on weak ones; a
board without R8 sees a level up to ~0.25 code below U80P64-R1 on odd
labels. docs/FINDINGS.md "Seven-bit DAC test".

Usage: p64_d10.py [check] | write | optimise
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

import p64_model

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = 'U80P64-D10'
DATA = ROOT / 'research/data/p64_d10.json'
HEADER = ROOT / 'main/p64d10_tables.h'
PROGRAM = ROOT / 'main/p64d10.bsasm'

# PARLIO TX lanes (bit k of each byte): D4..D9, then D10. 200 Ohm load.
LANE_OHMS = (8200.0, 3900.0, 2000.0, 1000.0, 470.0, 240.0, 15000.0)
LOAD_OHMS = 200.0


def node_volts(lanes, ohms=LANE_OHMS, vdd=3.3):
    """Nominal loaded node voltage; pin driver resistance ignored."""
    g = [1.0 / r for r in ohms]
    on = sum(gk for k, gk in enumerate(g) if lanes & (1 << k))
    return vdd * on / (sum(g) + 1.0 / LOAD_OHMS)


LANE_VOLTS = np.array([node_volts(v) for v in range(128)])
SIX_VOLTS = np.array([node_volts(v, LANE_OHMS[:6]) for v in range(64)])
UNIT = (LANE_VOLTS[63] - LANE_VOLTS[0]) / 63.0   # one six-bit code, volts


def p64_targets():
    """U80P64-R1 table and the unrounded six-bit level of every entry."""
    params = json.loads((ROOT / 'research/data/p64_repair.json').read_text())['params']
    table = p64_model.build_lut64_from_params(params).astype(np.int64)
    n_sync = int(params['n_sync'])
    n_vid = 64 - n_sync
    lo = 2 * math.pi * (-6.0e6) * 50e-9
    hi = 2 * math.pi * (+9.0e6) * 50e-9
    ph = np.r_[(np.arange(n_sync) + 0.5) * 2 * math.pi / n_sync - math.pi,
               (np.arange(n_vid) + 0.5) * 2 * math.pi / n_vid - math.pi]
    target = np.zeros(1024)
    for entry in range(1024):
        s, nxt = entry >> 4, (int(table[entry]) >> 6) & 63
        advance = (ph[nxt] - ph[s] + math.pi) % (2 * math.pi) - math.pi
        target[entry] = float(np.clip((advance - lo) / (hi - lo) * 63, 0, 63))
    # The repair emits the rounded value of exactly this level.
    assert np.array_equal(table & 63, np.rint(target).astype(np.int64))
    return table, target


P64, TARGET = p64_targets()
NXT = (P64 >> 6) & 63
_PAIR = np.array([[0.5 * (LANE_VOLTS[c] + LANE_VOLTS[c | (d << 6)]) for c in range(64)] for d in (0, 1)])
_WANT = LANE_VOLTS[0] + TARGET * UNIT
CODE = np.argmin(np.abs(_PAIR[:, None, :] - _WANT[None, :, None]), axis=2)    # [d, entry]
ERROR = (np.take_along_axis(_PAIR, CODE, axis=1) - _WANT[None, :]) / UNIT     # six-bit codes


def build_table(labels):
    """labels[old state] = new state number. Tokens (bits 12..15) belong to
    the observation address and stay; rows and next-state fields move."""
    labels = np.asarray(labels, dtype=np.int64)
    assert sorted(labels.tolist()) == list(range(64))
    lut = P64 & 0xf000
    for entry in range(1024):
        s, t, nxt = entry >> 4, entry & 15, int(NXT[entry])
        new = int(labels[nxt])
        lut[(labels[s] << 4) | t] |= int(CODE[new & 1, entry]) | (new << 6)
    return lut.astype(np.uint16)


def level_rms(labels, weights):
    e = ERROR[labels[NXT] & 1, np.arange(1024)]
    return float(np.sqrt(np.sum(weights * e * e) / np.sum(weights)))


@njit(cache=True)
def pair_lanes(raw, lut):
    """Two LUT16 lookups per pair as main/p64d10.bsasm: byte A = L0..L5,
    byte B = L0..L5 plus lane 6 = L6 (next-state label bit 0)."""
    out = np.zeros(len(raw), np.uint8)
    bits = (4, 5, 6, 7, 0, 1, 2, 3, 15, 11)
    state = 0
    for k in range(len(raw) // 2 - 1):
        word = np.int64(raw[2 * k]) | (np.int64(raw[2 * k + 1]) << 8)
        address = 0
        for j in range(10):
            address |= ((word >> bits[j]) & 1) << j
        value = np.int64(lut[(state << 4) + (np.int64(lut[address]) >> 12)])
        state = (value >> 6) & 63
        out[2 * k + 2] = value & 63
        out[2 * k + 3] = value & 127
    return out


@njit(cache=True)
def entry_visits(raw, lut):
    count = np.zeros(1024)
    bits = (4, 5, 6, 7, 0, 1, 2, 3, 15, 11)
    state = 0
    for k in range(len(raw) // 2 - 1):
        word = np.int64(raw[2 * k]) | (np.int64(raw[2 * k + 1]) << 8)
        address = 0
        for j in range(10):
            address |= ((word >> bits[j]) & 1) << j
        entry = (state << 4) + (np.int64(lut[address]) >> 12)
        count[entry] += 1
        state = (np.int64(lut[entry]) >> 6) & 63
    return count


def optimise(seed=1, iterations=40000):
    """Label search on entry visit weights from training seeds 311/991
    (PAL/NTSC, deviation 0.7/1.4, 40/20 dB C/N). Swap moves only."""
    import sim
    weights = np.zeros(1024)
    for case_seed in (311, 991):
        for standard in ('PAL', 'NTSC'):
            for deviation in (0.7, 1.4):
                for cn in (40.0, 20.0):
                    case = sim.make_case(case_seed, cn, standard, deviation, filter_hz=12e6,
                                         fields=1, physical_adc=True, adc_gain=3.0)
                    weights += entry_visits(case['raw'], P64.astype(np.uint16))
    rng = np.random.default_rng(seed)
    labels = np.arange(64)
    best = level_rms(labels, weights)
    for _ in range(iterations):
        i, j = rng.integers(0, 64, 2)
        if (labels[i] ^ labels[j]) & 1 == 0:
            continue
        labels[i], labels[j] = labels[j], labels[i]
        cost = level_rms(labels, weights)
        if cost < best:
            best = cost
        else:
            labels[i], labels[j] = labels[j], labels[i]
    return labels, best, level_rms(np.arange(64), weights)


def labels():
    return np.array(json.loads(DATA.read_text())['labels'], dtype=np.int64)


def table():
    return build_table(labels())


def digest(lut):
    return hashlib.sha256(struct.pack('<1024H', *(int(v) for v in lut))).hexdigest()


def checked_in_table():
    text = HEADER.read_text()
    values = text.split('p64d10_table[1024] = {', 1)[1].split('}', 1)[0]
    return np.array([int(v, 16) for v in re.findall(r'0x[0-9a-fA-F]+', values)], dtype=np.uint16)


def render_header(lut):
    rows = ',\n    '.join(', '.join(f'0x{int(v):04x}' for v in lut[k:k + 12]) for k in range(0, 1024, 12))
    return f'''/* C5VRX by Twotoz/contributors: {MODEL_ID}, U80P64-R1 with the D10 branch.
 * Generated by research/p64_d10.py from research/data/p64_d10.json.
 * Same 64-state tracker (relabelled), bits 0..5 DAC, 6..11 next state,
 * 12..15 token; byte B drives D10 from bit 6. Host model only; board test
 * pending. docs/FINDINGS.md "Seven-bit DAC test".
 */
#pragma once
#include <stdint.h>
#define P64D10_MODEL_ID "{MODEL_ID}"
#define P64D10_TABLE_ENTRIES 1024
#define P64D10_TABLE_SHA256 "{digest(lut)}"
static const uint16_t p64d10_table[1024] = {{
    {rows}
}};
'''


def render_program(lut):
    """main/p64.bsasm with byte B lane 6 = L6 and its bit 15 left low."""
    text = (ROOT / 'main/p64.bsasm').read_text()
    head, rest = text.split('\nlut ', 1)
    rest = rest.split('\n', 1)[1]
    assert rest.count('    set 14..15 L12..L13,\n') == 4
    rest = rest.replace('    set 14..15 L12..L13,\n', '    set 14 L6,\n')
    head = (f'# C5VRX by Twotoz/contributors: {MODEL_ID}, the U80P64-R1 tracker with the seventh DAC\n'
            '# branch: byte B lane 6 (D10) = L6, the next-state label bit 0. Generated by\n'
            '# research/p64_d10.py from main/p64.bsasm; do not edit.\n' +
            '\n'.join(head.split('\n')[2:]))
    return head + '\nlut ' + ' '.join(str(int(v)) for v in lut) + '\n' + rest


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if command == 'optimise':
        found, cost, before = optimise()
        data = json.loads(DATA.read_text()) if DATA.exists() else {}
        data.update(model_id=MODEL_ID, labels=[int(v) for v in found],
                    level_rms_codes=dict(identity=round(before, 4), optimised=round(cost, 4)))
        DATA.write_text(json.dumps(data, indent=1) + '\n')
        print(f'labels written: level rms {before:.4f} -> {cost:.4f} six-bit codes')
    elif command == 'write':
        lut = table()
        HEADER.write_text(render_header(lut))
        PROGRAM.write_text(render_program(lut))
        print('wrote', HEADER.name, PROGRAM.name, digest(lut))
    else:
        lut = table()
        assert np.array_equal(lut, checked_in_table()), 'p64d10_tables.h differs from research/data/p64_d10.json'
        assert PROGRAM.read_text() == render_program(lut), 'p64d10.bsasm differs from the generator'
        print(f'PASS: {MODEL_ID} header and program reproduce ({digest(lut)})')
