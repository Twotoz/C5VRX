#!/usr/bin/env python3
"""Simulate Candidate G BitScrambler program with bs_model.py."""

import sys
from pathlib import Path
import numpy as np

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
sys.path.append(str(ROOT / "tools"))
from bs_model import simulate

from test_lut_training_objectives import lut_median, curr_table, prev_table, curr_bits, prev_bits

# Generate Candidate G assembly text
lut_str = " ".join(str(int(v)) for v in lut_median)

asm_text = f"""cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 8

lut {lut_str}

prime_even:
    set 8..9 2..3,
    set 10..12 5..7,
    set 16..18 1..3,
    set 19..21 5..7,
    set 22..26 O8..O12,
    set 27..31 O27..O31,
    read 8

prime_odd:
    set 0..5 L0..L5,
    set 6..7 L,
    set 27..28 2..3,
    set 29..31 5..7,
    set 8..12 O8..O12,
    set 16..18 1..3,
    set 19..21 5..7,
    set 22..26 O27..O31,
    read 8,
    write 8

step_even:
    set 0..5 L0..L5,
    set 6..7 L,
    set 16..18 1..3,
    set 19..21 5..7,
    set 22..26 O8..O12,
    set 8..9 2..3,
    set 10..12 5..7,
    set 27..31 O27..O31,
    read 8,
    write 8

step_odd:
    set 0..5 L0..L5,
    set 6..7 L,
    set 16..18 1..3,
    set 19..21 5..7,
    set 22..26 O27..O31,
    set 27..28 2..3,
    set 29..31 5..7,
    set 8..12 O8..O12,
    read 8,
    write 8,
    jmp step_even
"""

# Test with 1000 bytes of raw capture
cap = np.fromfile(ROOT / "measurements/issue-11-cvbs/raw-vtx-on.bin", dtype=np.uint8)[:1000]

print("Simulating Candidate G assembly on 1000 raw samples...")
sim_out = simulate(asm_text, list(cap), 990)
print(f"Simulation completed! Output length: {len(sim_out)}")
print(f"Sample outputs: {sim_out[:20]}")
print(f"Min: {min(sim_out)}, Max: {max(sim_out)}")
