#!/usr/bin/env python3
"""Test bit-exact register matching for Candidate H-7+4."""

import sys
from pathlib import Path
import numpy as np

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
sys.path.append(str(ROOT / "tools"))
sys.path.append(str(ROOT / "scratch"))
from bs_model import simulate

from compare_h_configs import build_and_test_h74, cap2, golden_matrix

r74 = build_and_test_h74()
lut = r74['lut']
lut_str = " ".join(str(int(v)) for v in lut)

# Bit order in O8..O11 (and O12..O15):
# bit 0 (O8 / O12):  fine bit 0 (L6)
# bit 1 (O9 / O13):  fine bit 1 (L7)
# bit 2 (O10 / O14): SignQ (from O27 / O29)
# bit 3 (O11 / O15): SignI (from O28 / O30)
#
# When read by `set 23..26 O8..O11`:
# O23 = O8  = fine0 (bit 0 of state)
# O24 = O9  = fine1 (bit 1 of state)
# O25 = O10 = SignQ (bit 2 of state)
# O26 = O11 = SignI (bit 3 of state)
#
# Combined with O16..O22 (curr7):
# (out >> 16) & 2047 = (State << 7) | curr7!
# EXACT MATCH TO OFFLINE MODEL!

asm_text = f"""cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 8

lut {lut_str}

prime_even:
    # Prime even: sample 0 (x0)
    # curr7 -> O16..O22: Q[3:1] -> O16..O18, I[3:0] -> O19..O22
    set 16..18 1..3,
    set 19..22 4..7,
    # Initial prev even state -> O23..O26 (0)
    set 23..26 L,
    # Save x0 raw signs: O27 = SignQ (bit 3), O28 = SignI (bit 7)
    set 27 3,
    set 28 7,
    # Clear completed states and temp odd signs
    set 8..11 L,
    set 12..15 L,
    set 29..30 L,
    set 31 L,
    read 8

prime_odd:
    # Prime odd: sample 1 (x1)
    # Write sample 0 (DAC code L0..L5 from prime_even lookup)
    set 0..5 L0..L5,
    set 6..7 L,
    # curr7 of sample 1 -> O16..O22
    set 16..18 1..3,
    set 19..22 4..7,
    # Initial prev odd state -> O23..O26 (0)
    set 23..26 L,
    # Complete Even State 0: fine phase in L6..L7 -> O8..O9, SignQ/I in O27..O28 -> O10..O11
    set 8..9 L6..L7,
    set 10..11 O27..O28,
    # Save x1 raw signs into O29..O30 (temp signs for odd)
    set 29 3,
    set 30 7,
    # Preserve temp even signs
    set 27..28 O27..O28,
    set 12..15 L,
    set 31 L,
    read 8,
    write 8

step_even:
    # Write sample 2k-1 (DAC code from step_odd lookup)
    set 0..5 L0..L5,
    set 6..7 L,
    # Set LUT address for sample 2k (Current Even):
    # curr7 -> O16..O22
    set 16..18 1..3,
    set 19..22 4..7,
    # prev even state -> O23..O26 (from O8..O11)
    set 23..26 O8..O11,
    # Complete Odd State (from x[2k-1]): fine in L6..L7 -> O12..O13, SignQ/I in O29..O30 -> O14..O15
    set 12..13 L6..L7,
    set 14..15 O29..O30,
    # Save x[2k] raw signs into O27..O28 (temp signs for even)
    set 27 3,
    set 28 7,
    # Preserve Even State O8..O11
    set 8..11 O8..O11,
    # Preserve Odd Temp Signs O29..O30
    set 29..30 O29..O30,
    set 31 L,
    read 8,
    write 8

step_odd:
    # Write sample 2k (DAC code from step_even lookup)
    set 0..5 L0..L5,
    set 6..7 L,
    # Set LUT address for sample 2k+1 (Current Odd):
    # curr7 -> O16..O22
    set 16..18 1..3,
    set 19..22 4..7,
    # prev odd state -> O23..O26 (from O12..O15)
    set 23..26 O12..O15,
    # Complete Even State (from x[2k]): fine in L6..L7 -> O8..O9, SignQ/I in O27..O28 -> O10..O11
    set 8..9 L6..L7,
    set 10..11 O27..O28,
    # Save x[2k+1] raw signs into O29..O30 (temp signs for odd)
    set 29 3,
    set 30 7,
    # Preserve Odd State O12..O15
    set 12..15 O12..O15,
    # Preserve Even Temp Signs O27..O28
    set 27..28 O27..O28,
    set 31 L,
    read 8,
    write 8,
    jmp step_even
"""

# Save to main/c5vrx2_wbfm_candidate_h.bsasm
asm_path = ROOT / "main/c5vrx2_wbfm_candidate_h.bsasm"
asm_path.write_text(asm_text)
print(f"Saved corrected Candidate H-7+4 program to {asm_path}")

# Run simulation on cap2 using bs_model.py
N_SIM = 1000
raw_bytes = list(cap2[:N_SIM + 10])
print(f"Simulating Candidate H-7+4 assembly on {N_SIM} real VTX samples using bs_model.py...")
sim_out = np.array(simulate(asm_text, raw_bytes, N_SIM), dtype=np.uint8)

# Offline python model simulation on the same slice:
def to_curr7(b):
    return (((b >> 4) & 0xF) << 3) | ((b & 0xE) >> 1)
raw_to_c7 = np.array([to_curr7(b) for b in range(256)], dtype=np.uint8)

# Offline stream matching exact initialization (state starts at 0):
offline_out = np.zeros(N_SIM, dtype=np.uint8)
e_st = 0
o_st = 0
for k in range(N_SIM):
    c = cap2[k]
    c7 = raw_to_c7[c]
    sq = (c >> 3) & 1
    si = (c >> 7) & 1
    pst = e_st if k % 2 == 0 else o_st
    addr = (int(pst) << 7) | int(c7)
    val = lut[addr]
    l67 = (val >> 6) & 3
    if k % 2 == 0: e_st = (si << 3) | (sq << 2) | l67
    else: o_st = (si << 3) | (sq << 2) | l67
    offline_out[k] = val & 0x3F

# Compare sim_out with offline_out:
diff = np.abs(sim_out.astype(int) - offline_out.astype(int))
print(f"\n=======================================================")
print(f" BIT-EXACT HARDWARE ASSEMBLY VERIFICATION")
print(f"=======================================================")
print(f"  Assembly output (first 10): {sim_out[:10]}")
print(f"  Offline output  (first 10): {offline_out[:10]}")
print(f"  Max difference across {N_SIM} samples: {np.max(diff)}")
print(f"  Exact Bit-for-Bit Matches: {np.sum(diff == 0)} / {N_SIM} ({np.sum(diff == 0)/N_SIM*100:.2f}%)")

# Compare with Golden target on steady state (from sample 2):
t_sim = golden_matrix[cap2[:N_SIM-2], cap2[2:N_SIM]]
err = np.abs(sim_out[2:].astype(int) - t_sim.astype(int))
print(f"\nAssembly Simulation Performance vs Golden Target:")
print(f"  MAE: {np.mean(err):.3f}")
print(f"  P95: {np.percentile(err, 95):.1f}")
print(f"  P99: {np.percentile(err, 99):.1f}")
print(f"  Errors >= 16: {np.sum(err >= 16)} ({np.sum(err >= 16)/len(err)*100:.2f}%)")
print(f"  Errors >= 32: {np.sum(err >= 32)} ({np.sum(err >= 32)/len(err)*100:.2f}%)")
diffs = np.abs(np.diff(sim_out.astype(int)))
print(f"  Consecutive Jumps >= 16: {np.sum(diffs >= 16)} ({np.sum(diffs >= 16)/len(diffs)*100:.2f}%)")
print(f"  Consecutive Jumps >= 32: {np.sum(diffs >= 32)} ({np.sum(diffs >= 32)/len(diffs)*100:.2f}%)")
