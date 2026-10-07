#!/usr/bin/env python3
"""Pinned optimized C5VRX table generation; offline optimizer is not run in CI."""
import json
from pathlib import Path
from compile_pair_demod import build,validate
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'tools/ovp56_codebook.json'
TARGET=ROOT/'firmware/programs/c5vrx4_ovp56.bsasm'


def load():
    data=json.loads(DATA.read_text());validate(data)
    assert data['name']=='OVP56' and data['current_tokens']==56
    return data


def generate():
    TARGET.write_text(build(load()),encoding='utf-8')
    print('Generated c5vrx4_ovp56.bsasm: pinned bounded video fit, LUT8/2KiB, two bundles')

if __name__=='__main__':generate()
