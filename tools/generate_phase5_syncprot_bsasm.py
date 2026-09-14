import math
from pathlib import Path
import sys

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")
sys.path.append(str(ROOT / "tools"))
import validate_phase5_quality as p5

def get_syncprot_dac_code(delta):
    if delta >= 0:
        # 100% bit-for-bit identical to Golden Phase5 for all positive deltas
        return max(0, min(63, 20 + p5.scale_real_sum(delta, 2)))
    elif delta >= -16:
        # delta in [-16, 0): gentle ramp from 20 down to 16
        # delta = 0 -> 20, delta = -8 -> 18, delta = -16 -> 16
        # Above sync slicer threshold (~11-12), eliminating false sync triggers from noise
        return 20 + int(round(delta * (4.0 / 16.0)))
    elif delta >= -34:
        # delta in [-34, -16): steep sync drop from 16 down to 0
        # delta = -16 -> 16, delta = -24 -> 8, delta = -32 -> 0, delta = -34 -> 0
        val = 16 + int(round((delta + 16) * (16.0 / 16.0)))
        return max(0, min(16, val))
    else:
        # delta < -34: out-of-band impulse noise / pre-emphasis overshoot spike
        # Bound to pedestal (18) so noise cannot create false sync tips
        return 18

lut = [0] * 1024

for prev_ph in range(32):
    for curr_ph in range(32):
        delta = p5.centroid_delta_phase8(prev_ph, curr_ph)
        dac_code = get_syncprot_dac_code(delta)
        lut[(prev_ph << 5) | curr_ph] = dac_code

for packed in range(256):
    ph5 = p5.phase5_state(packed)
    lut[packed] |= (ph5 & 0x1F) << 8

# Verify Golden Phase5 identity on positive deltas
for prev_ph in range(32):
    for curr_ph in range(32):
        delta = p5.centroid_delta_phase8(prev_ph, curr_ph)
        if delta >= 0:
            expected = max(0, min(63, 20 + p5.scale_real_sum(delta, 2)))
            actual = lut[(prev_ph << 5) | curr_ph] & 0x3F
            assert actual == expected, f"Mismatch at {prev_ph}->{curr_ph} (delta={delta}): {actual} vs {expected}"

# Read the template assembly instructions from c5vrx2_wbfm_q4_phase5_2to1.bsasm
orig_bsasm = (ROOT / "main/c5vrx2_wbfm_q4_phase5_2to1.bsasm").read_text(encoding="utf-8")
instructions_start = orig_bsasm.find("address_phase:")
instructions_part = orig_bsasm[instructions_start:]

bsasm_content = f"""# Golden Phase5 WBFM core with Sync-Protected Discriminator LUT
# 40 MS/s packed Q4/I4 input and 40 MS/s (20 MS/s 2-hold) six-bit CVBS output.
#
# LUT layout (16-bit words):
#   LUT[raw_q4_i4].bits[12:8] = uniform five-bit polar phase
#   LUT[(previous_phase << 5) | current_phase].bits[5:0] = 6-bit DAC code
#
# Sync-Protected transfer function:
#   delta >= 0:   100% bit-for-bit identical to Golden Phase5 (maximum sharpness & chroma)
#   delta in [-16, 0):  Ramps 20 -> 16 (safely above sync slicer threshold ~11-12)
#   delta in [-34, -16): Steep sync edge from 16 down to 0 at delta = -32 (true sync tip)
#   delta < -34:  Clamped to 18 (pedestal) to reject out-of-band impulse noise false syncs

cfg prefetch true
cfg eof_on upstream
cfg trailing_bytes 9
cfg lut_width_bits 16

lut {" ".join(map(str, lut))}

{instructions_part}"""

target = ROOT / "main/c5vrx2_wbfm_q4_phase5_synclock_2to1.bsasm"
target.write_text(bsasm_content, encoding="utf-8")
print(f"Generated {target} successfully!")
