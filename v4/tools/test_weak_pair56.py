#!/usr/bin/env python3
"""Exhaustive source-model regression for the pinned experimental WVP56 LUT.

Standard library only. This validates a research candidate, not runtime routing,
physical LUT timing, RF sensitivity or board/goggle acceptance.
"""
import json
from pathlib import Path
import bs_model as bs
from compile_pair_demod import build, validate


def main():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root/'tools/detector_study/models/weak_pair56.json').read_text())
    validate(data)
    assert data['name'] == 'WVP56' and data['clean_training_max_error'] <= 1
    assert data['holdout_level_error'] <= 1
    source = build(data)
    cfg, lut, blocks, labels = bs.parse(source)
    assert cfg['lut_width_bits'] == '8' and len(lut) == 2048
    assert len(blocks) == len(labels) == 8
    enc, table = data['encoder'], data['map']
    raw = bytearray()
    for prev in range(256):
        for cur in range(256):
            raw.extend((prev, (prev*73+cur*29)&255, cur, 255))
    raw.extend(bytes(8))
    stats = {}
    out = bs.simulate(source, raw, len(raw), stats=stats, wrap_rom=True)
    assert stats['bundles'] == len(out)-1
    assert all(a == b and a < 64 for a, b in zip(out[::2], out[1::2]))
    for pair in range(65536):
        a, b = pair >> 8, pair & 255
        assert out[4*pair+4] == table[enc[a] >> 1][enc[b]]
    for initial in (0, 0xffff, 0xcccc):
        out = bs.simulate(source, raw[:512], 512, initial=(0, 0, initial, 0), wrap_rom=True)
        tokens = [enc[x] for x in raw[:512:2]]
        assert all(out[2*k+4] == table[tokens[k] >> 1][tokens[k+1]] for k in range(len(tokens)-2))
    print('PASS experimental WVP56: all 65536 raw pairs, arbitrary startup, LUT8/2KiB, two bundles, duplicate DAC6')


if __name__ == '__main__':
    main()
