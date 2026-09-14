#!/usr/bin/env python3
"""Generate c5vrx2_wbfm_q4_phase5_ped25_2to1.bsasm with Pedestal 25."""

import sys
from pathlib import Path

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
sys.path.append(str(ROOT / "tools"))
from validate_phase5_quality import build_lut

# Build LUT with calibration_gain=2 and pedestal=25
lut_ped25 = build_lut(calibration_gain=2, pedestal=25)
lut_str = " ".join(str(v) for v in lut_ped25)

# Read base assembly instructions from c5vrx2_wbfm_q4_phase5_2to1.bsasm
orig_text = (ROOT / "main/c5vrx2_wbfm_q4_phase5_2to1.bsasm").read_text()

# Extract header and instructions (everything after the lut line)
lines = orig_text.splitlines()
lut_line_idx = [i for i, l in enumerate(lines) if l.startswith("lut ")][0]
header = "\n".join(lines[:lut_line_idx])
instructions = "\n".join(lines[lut_line_idx+1:])

new_bsasm = f"""{header}
lut {lut_str}
{instructions}
"""

out_path = ROOT / "main/c5vrx2_wbfm_q4_phase5_ped25_2to1.bsasm"
out_path.write_text(new_bsasm)
print(f"Written {out_path} with {len(lut_ped25)} LUT words.")
