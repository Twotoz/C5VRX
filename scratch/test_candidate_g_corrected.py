#!/usr/bin/env python3
"""Candidate G with corrected register allocation and verified 1-cycle LUT pipeline:
- O8..O12  = persistent Prev_Even (5 bits)
- O13..O17 = persistent Prev_Odd (5 bits)
- O21..O26 = CurrentFeature6 (low 6 bits of 11-bit LUT address)
- O27..O31 = selected PrevState5 (high 5 bits of 11-bit LUT address)
- O0..O5   = DAC output (Byte 0)
"""

import sys
from pathlib import Path
import numpy as np

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
sys.path.append(str(ROOT / "tools"))

# Update bs_model.py LUT addressing for 8-bit width:
# In bs_model.py, line 81: look = lut[(out >> 16) & (len(lut)-1)]
# For 8-bit width with 2048 entries, address bits are O21..O31: (out >> 21) & 2047
# Let's verify this in the assembly structure.

from test_lut_training_objectives import lut_median

# 1. Feature extractors
# Curr 6 bits: bits 1, 2, 3 (Q3) and bits 5, 6, 7 (I3) -> O21..O26
# Prev 5 bits: bits 2, 3 (Q2) and bits 5, 6, 7 (I3) -> saved in O8..O12 (even) or O13..O17 (odd)
# When addressing: copied to O27..O31

lut_str = " ".join(str(int(v)) for v in lut_median)

asm_text = f"""cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 8

lut {lut_str}

prime_even:
    # Prime even: sample 0 (x0)
    # Save x0 5-bit feature into O8..O12 (Prev_Even)
    set 8..9 2..3,          # Q2
    set 10..12 5..7,        # I3
    # Preserve Prev_Odd in O13..O17
    set 13..17 O13..O17,
    # Set LUT address for sample 0:
    set 21..23 1..3,        # curr Q3
    set 24..26 5..7,        # curr I3
    set 27..31 O8..O12,     # prev even (initially 0)
    read 8

prime_odd:
    # Prime odd: sample 1 (x1)
    # Write sample 0 (from prime_even lookup)
    set 0..5 L0..L5,
    set 6..7 L,
    # Save x1 5-bit feature into O13..O17 (Prev_Odd)
    set 13..14 2..3,        # Q2
    set 15..17 5..7,        # I3
    # Preserve Prev_Even in O8..O12
    set 8..12 O8..O12,
    # Set LUT address for sample 1:
    set 21..23 1..3,        # curr Q3
    set 24..26 5..7,        # curr I3
    set 27..31 O13..O17,    # prev odd (initially 0)
    read 8,
    write 8

step_even:
    # Write sample 1 (from prime_odd or previous step_odd)
    set 0..5 L0..L5,
    set 6..7 L,
    # Set LUT address for sample 2k (Current Even):
    set 21..23 1..3,        # curr Q3 from x[2k]
    set 24..26 5..7,        # curr I3 from x[2k]
    set 27..31 O8..O12,     # prev even state (from x[2k-2])
    # Update Prev_Even for next even sample (2k+2):
    set 8..9 2..3,          # Q2 from x[2k]
    set 10..12 5..7,        # I3 from x[2k]
    # Preserve Prev_Odd:
    set 13..17 O13..O17,
    read 8,
    write 8

step_odd:
    # Write sample 2k (from step_even lookup)
    set 0..5 L0..L5,
    set 6..7 L,
    # Set LUT address for sample 2k+1 (Current Odd):
    set 21..23 1..3,        # curr Q3 from x[2k+1]
    set 24..26 5..7,        # curr I3 from x[2k+1]
    set 27..31 O13..O17,    # prev odd state (from x[2k-1])
    # Update Prev_Odd for next odd sample (2k+3):
    set 13..14 2..3,        # Q2 from x[2k+1]
    set 15..17 5..7,        # I3 from x[2k+1]
    # Preserve Prev_Even:
    set 8..12 O8..O12,
    read 8,
    write 8,
    jmp step_even
"""

print("Generated corrected Candidate G assembly.")
print(f"Assembly length: {len(asm_text.splitlines())} lines.")

# Save to main/c5vrx2_wbfm_interleaved40_direct11.bsasm
asm_path = ROOT / "main/c5vrx2_wbfm_interleaved40_direct11.bsasm"
asm_path.write_text(asm_text)
print(f"Written to {asm_path}")
