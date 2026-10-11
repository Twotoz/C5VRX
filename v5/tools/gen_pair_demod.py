#!/usr/bin/env python3
"""Analytic pair decoder for C5VRX by Twotoz and the contributors.

Extends PR #190 untrained_search.py/compile_overlay.py at 7491a660e267.
Two lookups, one shared 1024x16 LUT, unchanged continuous TX schedule.
The extra sample contributes a bounded direction vector, rotated back to
sample A's time using a fixed carrier prior. No LUT entries are data-fitted.
"""
import argparse
from pathlib import Path
import numpy as np


def pair_bits(layout):
    if len(layout) != 4 or any(c not in '1234' for c in layout) or sum(map(int, layout)) != 10:
        raise ValueError('four kept-bit widths must total ten')
    return np.array([bit for keep, base in zip(map(int, layout), (4, 0, 12, 8))
                     for bit in range(base + 4 - keep, base + 4)], dtype=np.int64)


def vectors(layout):
    a = np.arange(1024)
    vals = []
    pos = 0
    for keep in map(int, layout):
        v = (a >> pos) & ((1 << keep) - 1)
        v = np.where(v >= (1 << (keep - 1)), v - (1 << keep), v)
        vals.append(v * (1 << (4 - keep)) + .5 * (1 << (4 - keep)))
        pos += keep
    return vals[0] + 1j * vals[1], vals[2] + 1j * vals[3]


def build(kp=.85, weight=0., rotation=0., layout='4411', quad=True, centroid=False, quad_gain=.5, output="advance", assist_radius=0., agreement="none"):
    za, zb = vectors(layout)
    w = weight * np.maximum(0., 1. - (abs(za) / assist_radius) ** 2) if assist_radius else weight
    direction = zb / abs(zb) * np.exp(-1j * rotation)
    cosine = np.real(za * np.conj(direction)) / abs(za)
    if agreement == "hard": w = w * (cosine > 0.)
    elif agreement == "cosine": w = w * np.maximum(0., cosine)
    elif agreement != "none": raise ValueError("unknown agreement law")
    z = za + w * direction
    angle = np.angle(z)
    sectors = 28 if quad else 32
    token = np.floor((angle + np.pi) * sectors / (2 * np.pi)).astype(int) % sectors
    if quad:
        origin = abs(za) < .8
        token[origin] = 28 + (np.floor((angle[origin] + np.pi) / (np.pi / 2)).astype(int) % 4)
    obs = (np.arange(32) + .5) * 2 * np.pi / sectors - np.pi
    if quad: obs[28:] = (np.arange(4) + .5) * np.pi / 2 - np.pi
    if centroid:
        for t in range(sectors):
            if np.any(token == t): obs[t] = np.angle(np.mean(np.exp(1j * angle[token == t])))
    phase = (np.arange(32) + .5) * 2 * np.pi / 32 - np.pi
    error = (obs[None, :] - phase[:, None] + np.pi) % (2 * np.pi) - np.pi
    gains = np.full(32, kp)
    if quad: gains[28:] = quad_gain
    nxt = np.floor((phase[:, None] + gains * error + np.pi) * 32 / (2 * np.pi)).astype(int) % 32
    advance = (phase[nxt] - phase[:, None] + np.pi) % (2 * np.pi) - np.pi
    if output == "innovation": advance = error
    elif output == "update": advance = gains * error
    elif output != "advance": raise ValueError("unknown output law")
    hz = advance / (2 * np.pi * 50e-9)
    code = np.rint(np.clip((hz + 6e6) / 15e6 * 63, 0, 63)).astype(int)
    return ((code | (nxt << 6)).ravel() | (token << 11)).astype(np.uint16)


def program(lut, layout='4411'):
    bits = pair_bits(layout)
    if np.shape(lut) != (1024,): raise ValueError('LUT must contain 1024 words')
    s = ('# C5VRX by Twotoz and the contributors: analytic pair-assisted tracker.\n'
         '# Raw IQ40 / 32 KiB ring / TX only / two lookups per 50 ns.\n'
         '# Unique CVBS20 duplicated at TX40; no CPU sample loop.\n'
         'cfg prefetch true\ncfg eof_on downstream\ncfg trailing_bytes 0\ncfg lut_width_bits 16\n'
         'lut ' + ' '.join(map(str, lut)) + '\n')
    for k in range(4):
        addr = '\n    '.join(f'set {16+j} {bit},' for j, bit in enumerate(bits))
        s += f'''controller_{k}:
    set 0..5 L0..L5,
    set 8..13 L0..L5,
    set 14..15 L12..L13,
    {addr}
    set 26..31 L6..L11,
    read 16,
    write 16,
    nop
worker_{k}:
    set 16..20 L11..L15,
    set 21..25 O26..O30,
    nop
'''
    return s


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check', action='store_true', help='verify checked-in U85G programs')
    a = p.parse_args()
    main = Path(__file__).resolve().parents[1] / 'main'
    for name, kp, qg in [('u85g', .85, .5)]:
        text = program(build(kp=kp, weight=1., rotation=.4, assist_radius=2.5, quad_gain=qg))
        target = main / (name + '.bsasm')
        if a.check:
            if target.read_text() != text: raise SystemExit(f'{target}: regenerate with gen_pair_demod.py')
        else: target.write_text(text)
