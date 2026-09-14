#!/usr/bin/env python3
"""Candidate H with physically proven O16..O26 address mapping:
Output register layout:
- O0..O5:   DAC output (Byte 0)
- O6..O7:   Constant/scratch
- O8..O11:  Even state (4 bits: SignI, SignQ, fine[1:0])
- O12..O15: Odd state (4 bits: SignI, SignQ, fine[1:0])
- O16..O22: Current sample feature 7 bits (Q[3:1] = 3 bits, I[3:0] = 4 bits)
- O23..O26: Selected previous state 4 bits (Even or Odd)
- O27..O31: Unused (set to 0)

Total address bits: O16..O26 (11 bits = 2048 entries)!
Index bit 0..6:  Current 7-bit feature
Index bit 7..10: Previous 4-bit state
Address in LUT = (prev_state << 7) | curr7!
Matches (out >> 16) & 2047 EXACTLY!
"""

import sys
from pathlib import Path
import numpy as np

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
sys.path.append(str(ROOT / "tools"))
from bs_model import simulate

from compare_h_configs import build_and_test_h74, cap2, golden_matrix

r74 = build_and_test_h74()
lut = r74['lut']
lut_str = " ".join(str(int(v)) for v in lut)

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
    # Save x0 raw signs into O8 (SignQ = bit 3) and O9 (SignI = bit 7)
    set 8 3,
    set 9 7,
    # Clear odd state in O12..O15
    set 12..15 L,
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
    # Complete Even State 0: SignQ/I in O8..O9, fine phase in L6..L7 -> O10..O11
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
    # curr7 -> O16..O22
    set 16..18 1..3,
    set 19..22 4..7,
    # prev even state -> O23..O26 (from O8..O11)
    set 23..26 O8..O11,
    # Complete Odd State (from x[2k-1]): SignQ/I in O12..O13, fine in L6..L7 -> O14..O15
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
    # curr7 -> O16..O22
    set 16..18 1..3,
    set 19..22 4..7,
    # prev odd state -> O23..O26 (from O12..O15)
    set 23..26 O12..O15,
    # Complete Even State (from x[2k]): SignQ/I in O8..O9, fine in L6..L7 -> O10..O11
    set 8..9 O8..O9,
    set 10..11 L6..L7,
    # Save x[2k+1] raw signs into O12..O13
    set 12 3,
    set 13 7,
    read 8,
    write 8,
    jmp step_even
"""

# Save to main/c5vrx2_wbfm_candidate_h.bsasm
asm_path = ROOT / "main/c5vrx2_wbfm_candidate_h.bsasm"
asm_path.write_text(asm_text)
print(f"Saved Candidate H-7+4 program to {asm_path}")

# Run simulation on cap2 using bs_model.py
N_SIM = 1000
raw_bytes = list(cap2[:N_SIM + 10])
print(f"Simulating Candidate H-7+4 assembly on {N_SIM} real VTX samples using bs_model.py...")
sim_out = np.array(simulate(asm_text, raw_bytes, N_SIM), dtype=np.uint8)

# Compare with Golden target
t_sim = golden_matrix[cap2[:N_SIM-2], cap2[2:N_SIM]]
# Note: simulate emits sample 0 at step 1, so output is aligned with input
print(f"Simulation completed! Output length: {len(sim_out)}")
print(f"Sample outputs: {sim_out[:20]}")
print(f"Target outputs: {t_sim[:20]}")

# Compare stream error:
err_sim = np.abs(sim_out[2:N_SIM].astype(int) - t_sim[:N_SIM-2].astype(int))
print(f"Simulated MAE vs Golden Target: {np.mean(err_sim):.3f}")
print(f"Simulated Max Error: {np.max(err_sim)}")
print(f"Simulated Errors >= 16: {np.sum(err_sim >= 16)} ({np.sum(err_sim >= 16)/len(err_sim)*100:.2f}%)")
print(f"Simulated Errors >= 32: {np.sum(err_sim >= 32)} ({np.sum(err_sim >= 32)/len(err_sim)*100:.2f}%)")

diffs = np.abs(np.diff(sim_out.astype(int)))
print(f"Simulated Consecutive Output Mean Jump: {np.mean(diffs):.2f}")
print(f"Simulated Jumps >= 16: {np.sum(diffs >= 16)} ({np.sum(diffs >= 16)/len(diffs)*100:.2f}%)")
print(f"Simulated Jumps >= 32: {np.sum(diffs >= 32)} ({np.sum(diffs >= 32)/len(diffs)*100:.2f}%)")
