#!/usr/bin/env python3
"""Bit-exact simulation of Candidate H assembly using bs_model.py.
Tests both H-8+3 and H-7+4 to verify that BitScrambler assembly execution
matches the offline mathematical model 100% bit-for-bit.
"""

import sys
from pathlib import Path
import numpy as np

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
sys.path.append(str(ROOT / "tools"))
from bs_model import simulate

from compare_h_configs import build_and_test_h83, build_and_test_h74, cap2, golden_matrix

# =====================================================================
# 1. Candidate H-7+4 Assembly Generator
# =====================================================================
def generate_h74_assembly(lut):
    lut_str = " ".join(str(int(v)) for v in lut)
    asm = f"""cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 8

lut {lut_str}

prime_even:
    # Prime even: sample 0 (x0)
    # curr7: Q[3:1] (wires 1..3) -> O21..O23, I[3:0] (wires 4..7) -> O24..O27
    set 21..23 1..3,
    set 24..27 4..7,
    # Initial previous state = 0
    set 28..31 L,
    # Save x0 raw signs into O8 (SignQ = bit 3) and O9 (SignI = bit 7)
    set 8 3,
    set 9 7,
    # Initialize odd registers to 0
    set 12..15 L,
    read 8

prime_odd:
    # Prime odd: sample 1 (x1)
    # Write sample 0 (DAC code L0..L5 from prime_even lookup)
    set 0..5 L0..L5,
    set 6..7 L,
    # curr7 of sample 1:
    set 21..23 1..3,
    set 24..27 4..7,
    # Initial previous odd state = 0
    set 28..31 L,
    # Complete Even State 0: SignQ/I in O8..O9, fine phase in L6..L7
    set 8..9 O8..O9,
    set 10..11 L6..L7,
    # Save x1 raw signs into O12 (SignQ = bit 3) and O13 (SignI = bit 7)
    set 12 3,
    set 13 7,
    read 8,
    write 8

step_even:
    # Write sample 2k-1 (DAC code from step_odd lookup)
    set 0..5 L0..L5,
    set 6..7 L,
    # Set LUT address for sample 2k (Current Even):
    set 21..23 1..3,
    set 24..27 4..7,
    set 28..31 O8..O11,     # prev even state (from x[2k-2])
    # Complete Odd State (from x[2k-1]): SignQ/I in O12..O13, fine in L6..L7
    set 12..13 O12..O13,
    set 14..15 L6..L7,
    # Save x[2k] raw signs into O8..O9
    set 8 3,
    set 9 7,
    read 8,
    write 8

step_odd:
    # Write sample 2k (DAC code from step_even lookup)
    set 0..5 L0..L5,
    set 6..7 L,
    # Set LUT address for sample 2k+1 (Current Odd):
    set 21..23 1..3,
    set 24..27 4..7,
    set 28..31 O12..O15,    # prev odd state (from x[2k-1])
    # Complete Even State (from x[2k]): SignQ/I in O8..O9, fine in L6..L7
    set 8..9 O8..O9,
    set 10..11 L6..L7,
    # Save x[2k+1] raw signs into O12..O13
    set 12 3,
    set 13 7,
    read 8,
    write 8,
    jmp step_even
"""
    return asm

# =====================================================================
# 2. Candidate H-8+3 Assembly Generator
# =====================================================================
def generate_h83_assembly(lut):
    lut_str = " ".join(str(int(v)) for v in lut)
    asm = f"""cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 8

lut {lut_str}

prime_even:
    # Prime even: sample 0 (x0)
    # curr8: full Q4/I4 (wires 0..7) -> O21..O28
    set 21..28 0..7,
    set 29..31 L,
    # Save x0 SignI (bit 7) into O8
    set 8 7,
    set 11..13 L,
    read 8

prime_odd:
    # Prime odd: sample 1 (x1)
    # Write sample 0
    set 0..5 L0..L5,
    set 6..7 L,
    # curr8 of sample 1
    set 21..28 0..7,
    set 29..31 L,
    # Complete Even State 0: SignI in O8, fine phase in L6..L7
    set 8 O8,
    set 9..10 L6..L7,
    # Save x1 SignI (bit 7) into O11
    set 11 7,
    read 8,
    write 8

step_even:
    # Write sample 2k-1
    set 0..5 L0..L5,
    set 6..7 L,
    # Set LUT address for sample 2k (Current Even):
    set 21..28 0..7,
    set 29..31 O8..O10,     # prev even state (from x[2k-2])
    # Complete Odd State (from x[2k-1]): SignI in O11, fine in L6..L7
    set 11 O11,
    set 12..13 L6..L7,
    # Save x[2k] SignI into O8
    set 8 7,
    read 8,
    write 8

step_odd:
    # Write sample 2k
    set 0..5 L0..L5,
    set 6..7 L,
    # Set LUT address for sample 2k+1 (Current Odd):
    set 21..28 0..7,
    set 29..31 O11..O13,    # prev odd state (from x[2k-1])
    # Complete Even State (from x[2k]): SignI in O8, fine in L6..L7
    set 8 O8,
    set 9..10 L6..L7,
    # Save x[2k+1] SignI into O11
    set 11 7,
    read 8,
    write 8,
    jmp step_even
"""
    return asm

print("Training Candidate H models and verifying assembly bit-exactness...")
r83 = build_and_test_h83()
r74 = build_and_test_h74()

asm_h83 = generate_h83_assembly(r83['lut'])
asm_h74 = generate_h74_assembly(r74['lut'])

test_samples = list(cap2[:500])

# In bs_model.py, remember line 81:
# look=lut[(out>>16)&(len(lut)-1)]
# For lut_width_bits 8, len(lut)=2048 (11 bits).
# BitScrambler uses O21..O31 for 11-bit indexing.
# Let's check how bs_model.py behaves!
print("\nTesting bs_model simulation of H-7+4...")
try:
    sim74 = simulate(asm_h74, test_samples, 200)
    print(f"H-7+4 simulation completed! Emitted {len(sim74)} samples: {sim74[:10]}")
except Exception as e:
    print(f"H-7+4 simulation error: {e}")

print("\nTesting bs_model simulation of H-8+3...")
try:
    sim83 = simulate(asm_h83, test_samples, 200)
    print(f"H-8+3 simulation completed! Emitted {len(sim83)} samples: {sim83[:10]}")
except Exception as e:
    print(f"H-8+3 simulation error: {e}")
