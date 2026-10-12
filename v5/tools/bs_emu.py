#!/usr/bin/env python3
"""Host emulator for the BitScrambler subset used by C5VRX-5 TX programs.

C5VRX by Twotoz and the C5VRX contributors. Models the ESP-IDF bsasm
semantics documented for ESP32-C5 (one bundle per clock): `set` from input
bits 0..63, previous output O0..O31, LUT16 output L0..L15 (address =
previous-cycle O16..O25), counters A/B and constants H/L; `read`/`write`
8/16/32; opcodes NOP, ADDCTI/LDCTI on A or B (full, H or L), sequential
slots wrapping after the last bundle. Unsupported syntax raises.

This is a host reference for programs whose dataflow must be checked
before hardware; it is not a timing model and not proof of silicon
behaviour. Its LUT/counter semantics are checked against the shipped U85Q
program (tests) and must be confirmed on the board.
"""
import re
from pathlib import Path
import numpy as np
from numba import njit

# source kinds
S_ZERO, S_ONE, S_IN, S_O, S_L, S_A, S_B = range(7)
OPS = {'nop': 0, 'addctia': 1, 'addctiah': 2, 'addctial': 3, 'ldctia': 4, 'ldctiah': 5, 'ldctial': 6,
       'addctib': 7, 'addctibh': 8, 'addctibl': 9, 'ldctib': 10, 'ldctibh': 11, 'ldctibl': 12}


def _source(tok):
    t = tok.lower()
    if t == 'h':
        return S_ONE, 0
    if t == 'l':
        return S_ZERO, 0
    m = re.fullmatch(r'([olab]?)(\d+)', t)
    if not m:
        raise ValueError(f'unsupported source {tok}')
    kind = {'': S_IN, 'o': S_O, 'l': S_L, 'a': S_A, 'b': S_B}[m.group(1)]
    return kind, int(m.group(2))


def _range(tok):
    if '..' in tok:
        a, b = tok.split('..')
        return a, b
    return tok, tok


def parse(text):
    """Return (lut uint16[1024], slots) where each slot is (kind[32], idx[32], op, read, write)."""
    if not re.search(r'^cfg lut_width_bits 16$', text, re.M):
        raise ValueError('emulator supports LUT16 programs only')
    lut = np.array([int(w, 0) for line in re.findall(r'^lut ([0-9xa-fA-F ,]+)$', text, re.M) for w in line.replace(',', ' ').split()], np.int64)
    if len(lut) > 1024:
        raise ValueError('LUT16 holds at most 1024 words')
    lut = np.concatenate([lut, np.zeros(1024 - len(lut), np.int64)])
    body = [re.sub(r'#.*', '', l).strip() for l in text.splitlines()]
    body = [l for l in body if l and not l.startswith(('cfg ', 'lut '))]
    slots, cur = [], None
    for line in body:
        if re.fullmatch(r'\S+:', line):
            continue
        if cur is None:
            cur = dict(kind=np.zeros(32, np.int64), idx=np.zeros(32, np.int64), op=0, read=0, write=0, used=set())
        more = line.endswith(',')
        sub = line.rstrip(',').strip()
        w = sub.split()
        if w[0] == 'set':
            o0, o1 = (int(x) for x in _range(w[1]))
            s0, s1 = _range(w[2])
            k0, i0 = _source(s0)
            k1, i1 = _source(s1)
            outs = list(range(o0, o1 + 1)) if o0 <= o1 else list(range(o0, o1 - 1, -1))
            if len(outs) > 1 and s0 != s1:
                if k0 != k1:
                    raise ValueError(f'mixed range {sub}')
                srcs = list(range(i0, i1 + 1)) if i0 <= i1 else list(range(i0, i1 - 1, -1))
                if len(srcs) != len(outs):
                    raise ValueError(f'range length {sub}')
            else:
                srcs = [i0] * len(outs)
            for o, s in zip(outs, srcs):
                if o in cur['used']:
                    raise ValueError(f'output {o} set twice')
                cur['used'].add(o)
                cur['kind'][o], cur['idx'][o] = k0, s
        elif w[0] in ('read', 'write'):
            cur[w[0]] = int(w[1])
        elif w[0] in OPS:
            cur['op'] = OPS[w[0]]
        else:
            raise ValueError(f'unsupported sub-instruction {sub}')
        if not more:
            slots.append(cur)
            cur = None
    if cur is not None:
        raise ValueError('unterminated bundle')
    kind = np.array([s['kind'] for s in slots])
    idx = np.array([s['idx'] for s in slots])
    meta = np.array([[s['op'], s['read'], s['write']] for s in slots], np.int64)
    return lut, kind, idx, meta


@njit(cache=True)
def _run(data, lut, kind, idx, meta, max_out):
    n = len(data)
    out = np.zeros(max_out, np.uint8)
    pos = 0
    reg = np.uint64(0)
    for k in range(8):                       # cfg prefetch true: 64 bits
        if pos < n:
            reg |= np.uint64(data[pos]) << np.uint64(8 * k)
        pos += 1
    o_prev = np.int64(0)
    a = np.int64(0)
    b = np.int64(0)
    nout = 0
    slot = 0
    nslots = kind.shape[0]
    while nout < max_out and pos < n + 8:
        L = lut[(o_prev >> 16) & 1023]
        o = np.int64(0)
        for bit in range(32):
            kd = kind[slot, bit]
            ix = idx[slot, bit]
            v = np.int64(0)
            if kd == 1:
                v = 1
            elif kd == 2:
                v = np.int64((reg >> np.uint64(ix)) & np.uint64(1))
            elif kd == 3:
                v = (o_prev >> ix) & 1
            elif kd == 4:
                v = (L >> ix) & 1
            elif kd == 5:
                v = (a >> ix) & 1
            elif kd == 6:
                v = (b >> ix) & 1
            o |= np.int64(v) << bit
        op, rd, wr = meta[slot, 0], meta[slot, 1], meta[slot, 2]
        hi = (o >> 16) & 0xffff
        if op == 1:
            a = (a + hi) & 0xffff
        elif op == 2:
            a = (a & 0xff) | ((((a >> 8) + (hi >> 8)) & 0xff) << 8)
        elif op == 3:
            a = (a & 0xff00) | ((a + hi) & 0xff)
        elif op == 4:
            a = hi
        elif op == 5:
            a = (a & 0xff) | (hi & 0xff00)
        elif op == 6:
            a = (a & 0xff00) | (hi & 0xff)
        elif op == 7:
            b = (b + hi) & 0xffff
        elif op == 8:
            b = (b & 0xff) | ((((b >> 8) + (hi >> 8)) & 0xff) << 8)
        elif op == 9:
            b = (b & 0xff00) | ((b + hi) & 0xff)
        elif op == 10:
            b = hi
        elif op == 11:
            b = (b & 0xff) | (hi & 0xff00)
        elif op == 12:
            b = (b & 0xff00) | (hi & 0xff)
        for k in range(wr // 8):
            if nout < max_out:
                out[nout] = (o >> (8 * k)) & 255
                nout += 1
        if rd:
            reg = reg >> np.uint64(rd)
            for k in range(rd // 8):
                v = np.uint64(data[pos]) if pos < n else np.uint64(0)
                reg |= np.uint64(v) << np.uint64(64 - rd + 8 * k)
                pos += 1
        o_prev = o
        slot += 1
        if slot == nslots:
            slot = 0
    return out[:nout]


def run(text_or_path, data):
    text = Path(text_or_path).read_text() if isinstance(text_or_path, Path) else text_or_path
    lut, kind, idx, meta = parse(text)
    return _run(np.asarray(data, np.uint8), lut, kind, idx, meta, len(data))
