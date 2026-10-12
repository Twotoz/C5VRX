#!/usr/bin/env python3
"""AWD6: adaptive-wrap phase-difference demodulator for C5VRX-5.

C5VRX by Twotoz and the C5VRX contributors. Extends the U85Q shared-LUT
tracker (PR #190), the counter-A phase subtraction proven on the board by
`main/bs_addctia_probe.bsasm` (C5VRX-3) and the counter-assisted Phase8
endpoint delta of C5VRX-3/4, and the predictive host references of #198.

Per 50 ns span (sample A of the raw PAIR, as in U85Q):

    theta  = 6-bit phase of the folded Q4/I4 cell          (LUT, bank 0)
    D      = theta_k - theta_(k-1) mod 64                  (counter A, high)
    e      = wrap(D/64 - c)          c: window centre, 6 levels
    output = DAC(c + e)  over the U85Q window -6..+9 MHz
    c'     = nearest level to c + b*e, or one level toward the middle
             when e is only reachable on the wrong branch     (LUT, banks 1..3)

D is the 50-ns endpoint phase step at 312.5 kHz resolution. The only
state is c: the +/-half-turn ambiguity of D is resolved around a tracked
centre instead of a fixed one. Six levels span -0.15..0.18 turn
(-3..+3.6 MHz), so in a sync tip the centre can follow it down and keep the
noise margin there. A centre that low could lock onto the wrong branch
when a white field follows a long sync stretch on a wide, off-centre VTX
(rendered black). The escape rule prevents it: below the middle (0.08
turn) an error below -0.35 turn -- impossible on the right branch, where
white is above the centre -- moves the centre up one level instead of
down (mirrored above the middle). Tests drive these cases through the
emulated program.

Schedule (two bundles per pair, read16 + write16 per pair, like U85Q):

  controller_j  L = AWD word of span j-1 -> write [code, code], next level
                in the unconnected DAC bits 7,14,15; address = next sample
                byte (theta bank 0); ADDCTIAH adds w_j = +/-theta_j.
  worker_j      L = theta word of the next sample -> keep its w in O0..O5;
                address = D_j (A10..A15) + span parity (constant of the slot)
                + level; LDCTIAH loads w_j.

In LUT16 mode counter B cannot be read together with L, so only counter A
is used. To avoid storing both +theta and -theta, w alternates sign with
span parity (w = +theta on even spans, -theta on odd): D_j = w_(j-1) + w_j
is +delta or -delta. The parity is an address bit set as a constant by
each program slot, so it can never become misaligned; at startup or after
any upset the level bits recover within one span (theta words carry a
default level). Counter bits 8..9 hold the level's two high bits (never
00, so AWD words never alias the theta bank); the ADD operand's bits 8..9
are 00, so no carry reaches the phase bits 10..15.

Everything is host/assembler evidence until measured on the board.
"""
import argparse
from pathlib import Path
import numpy as np

STEPS = 64                       # phase / delta resolution
DT = 50e-9                       # one D step = 1/64 turn per 50 ns = 312.5 kHz
LO_HZ, HI_HZ = -6e6, 9e6         # U85Q DAC window (AFC porch at -1 MHz)
LEVELS = 6                       # centre levels: (lvl0, lvl_hi) with lvl_hi in 1..3
DEFAULT = dict(lo=-.15, hi=.18, b=.15, esc=.35, mid=.08)


def theta6(byte):
    """6-bit phase of a folded Q4/I4 byte (I high nibble, Q low nibble)."""
    i = (byte >> 4) & 15
    q = byte & 15
    i = np.where(i >= 8, i - 16, i) + .5
    q = np.where(q >= 8, q - 16, q) + .5
    return (np.rint(np.arctan2(q, i) / (2 * np.pi) * STEPS).astype(np.int64)) % STEPS


def dac(turns):
    hz = np.asarray(turns) / DT
    return np.rint(np.clip((hz - LO_HZ) / (HI_HZ - LO_HZ) * 63, 0, 63)).astype(np.int64)


def wrap(x):
    return x - np.floor(x + .5)


def levels(lo=DEFAULT['lo'], hi=DEFAULT['hi']):
    return np.linspace(lo, hi, LEVELS)


def transition(v, b=DEFAULT['b'], esc=DEFAULT['esc'], mid=DEFAULT['mid']):
    """e and next level index for every (level, D) in the plain (even) form."""
    d = np.arange(STEPS) / STEPS
    e = wrap(d[None, :] - v[:, None])
    nxt = np.argmin(np.abs(v[None, None, :] - (v[:, None] + b * e)[:, :, None]), axis=2)
    low = (v[:, None] < mid) & (e < -esc)
    high = (v[:, None] > mid) & (e > esc)
    index = np.arange(len(v))[:, None] + np.zeros_like(nxt)
    nxt = np.where(low, index + 1, np.where(high, index - 1, nxt))
    return e, nxt


def level_bits(li):
    """Level index -> (lvl0, lvl_hi) with lvl_hi in 1..3 (never the theta bank)."""
    return li & 1, 1 + (li >> 1)


def build(lo=DEFAULT['lo'], hi=DEFAULT['hi'], b=DEFAULT['b'], esc=DEFAULT['esc']):
    v = levels(lo, hi)
    e, nxt = transition(v, b, esc)
    code = dac(v[:, None] + e)
    lut = np.zeros(1024, np.int64)
    t = theta6(np.arange(256))
    l0, lh = level_bits(LEVELS // 2)
    default = l0 | (lh << 1)                         # 3 bits, valid level
    lut[:256] = t | (default << 6) | (((-t) % STEPS) << 9)
    for parity in (0, 1):
        d = np.arange(STEPS)
        delta = d if parity == 0 else (-d) % STEPS   # odd spans see D = -delta
        for li in range(LEVELS):
            l0, lh = level_bits(li)
            address = d | (parity << 6) | (l0 << 7) | (lh << 8)
            n0, nh = level_bits(nxt[li, delta])
            lut[address] = code[li, delta] | ((n0 | (nh << 1)) << 6)
    return lut.astype(np.uint16)


def model(raw, lo=DEFAULT['lo'], hi=DEFAULT['hi'], b=DEFAULT['b'], esc=DEFAULT['esc']):
    """Plain reference with the same arithmetic: output pair k holds the code
    for pair k-1 -> k, one span later than U85Q's timing in sim.py."""
    v = levels(lo, hi)
    e, nxt = transition(v, b, esc)
    code = dac(v[:, None] + e)
    t = theta6(np.asarray(raw[0::2], np.int64))
    out = np.zeros(len(raw), np.uint8)
    li = LEVELS // 2
    for k in range(1, len(t)):
        d = (t[k] - t[k - 1]) % STEPS
        c = code[li, d]
        li = nxt[li, d]
        if 2 * k + 5 < len(raw):
            out[2 * k + 4] = c
            out[2 * k + 5] = c
    return out


def program(lut):
    if np.shape(lut) != (1024,):
        raise ValueError('LUT must contain 1024 words')
    s = ('# C5VRX by Twotoz and the contributors: AWD6 adaptive-wrap demodulator.\n'
         '# Raw IQ40 / TX only / two bundles, read16 + write16 per 50 ns pair.\n'
         '# Counter A high byte: 6-bit endpoint phase step; unique CVBS20 at TX40.\n'
         '# Generated by tools/gen_awd_demod.py; hardware timing still to be measured.\n'
         'cfg prefetch true\ncfg eof_on downstream\ncfg trailing_bytes 0\ncfg lut_width_bits 16\n'
         'lut ' + ' '.join(map(str, lut)) + '\n')
    for j in range(4):
        w = 'L9..L14' if (j + 1) % 2 else 'L0..L5'     # w of the span adding this sample
        parity = 'H' if j % 2 else 'L'
        s += f'''controller_{j}:
    set 0..5 L0..L5,
    set 8..13 L0..L5,
    set 7 L6,
    set 14..15 L7..L8,
    set 16..23 0..7,
    set 24..25 L,
    set 26..31 O0..O5,
    read 16,
    write 16,
    addctiah
worker_{j}:
    set 0..5 {w},
    set 16..21 A10..A15,
    set 22 {parity},
    set 23 O7,
    set 24..25 O14..O15,
    set 26..31 O26..O31,
    ldctiah
'''
    return s


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--check', action='store_true', help='verify the checked-in program')
    a = p.parse_args()
    target = Path(__file__).resolve().parents[1] / 'main' / 'awd6.bsasm'
    text = program(build())
    if a.check:
        if target.read_text() != text:
            raise SystemExit(f'{target}: regenerate with gen_awd_demod.py')
    else:
        target.write_text(text)
