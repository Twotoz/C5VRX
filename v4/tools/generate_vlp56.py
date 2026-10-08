#!/usr/bin/env python3
"""C5VRX by Twotoz and contributors: deterministic native VLP56 generator.

Codebook training is offline. Firmware builds need only the Python standard
library and the pinned, reviewable JSON table. No root-main dependency.
"""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'tools/vlp56_codebook.json'
TARGET = ROOT / 'firmware/programs/c5vrx4_vlp56.bsasm'


def load():
    data = json.loads(DATA.read_text())
    enc, table = data['encoder'], data['map']
    assert len(enc) == 256 and all(type(t) is int and 0 <= t < 56 for t in enc)
    assert len(table) == 28 and all(len(row) == 56 for row in table)
    assert all(type(t) is int and 0 <= t < 64 for row in table for t in row)
    assert data['span_ns'] == 50
    return data


def build():
    data = load()
    lut = [0] * 2048
    for previous, row in enumerate(data['map']):
        lut[previous * 64:previous * 64 + 56] = row
    lut[1792:] = data['encoder']
    source = '''# C5VRX by Twotoz and contributors: VLP56 bounded pair-FM experiment.
# Source Q4/I4 RX40; raw32K ring; two TX bundles per 50-ns endpoint pair.
# Current token56, retained previous token28; direct calibrated pair->DAC6.
# LUT8: [0..1791] pair rows, [1792..2047] raw encoder. Exactly 2048 bytes.
# Unique CVBS20, [D,D] physical TX40. No CPU per-sample path or resets at DMA wraps.
# Codebook, frequency bounds and simulation limitations: PAIR_DEMOD_STUDY.md.
# The first two output pairs after program load are pipeline startup, not video.
cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 8
'''
    source += 'lut ' + ' '.join(map(str, lut)) + '\n'
    for n in range(4):
        source += f'''
controller_{n}:
    # Previous pair result; address this pair's first raw IQ endpoint.
    set 0..5 L0..L5,
    set 8..13 L0..L5,
    set 16..23 0..7,
    set 24..26 H,
    read 16,
    write 16,
    nop
worker_{n}:
    # Index = previous28 * 64 + current56. B[5:0] retains current56.
    set 16..21 L0..L5,
    set 22..26 B1..B5,
    ldctib
'''
    return source


def generate():
    TARGET.write_text(build(), encoding='utf-8')
    print(f'Generated {TARGET.name}: LUT8, two bundles, bounded pair FM')

if __name__ == '__main__':
    generate()
