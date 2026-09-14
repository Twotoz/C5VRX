import math
from pathlib import Path

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")

PI_F = 3.14159265358979323846

s_phase5_centroid_phase8 = [
       0,    8,   15,   24,   32,   40,   49,   56,
      64,   72,   79,   87,   96,  104,  113,  120,
    -128, -120, -113, -104,  -96,  -88,  -79,  -72,
     -64,  -56,  -49,  -40,  -32,  -23,  -15,   -8,
]

def signed_bucket_center(code, bits):
    width = 1 << (10 - bits)
    center = float(code * width) + (float(width) - 1.0) * 0.5
    if center >= 512.0:
        center -= 1024.0
    return center

def q4_phase5(packed):
    q = signed_bucket_center(packed & 0x0F, 4)
    i = signed_bucket_center(packed >> 4, 4)
    phase5 = int(round(math.atan2(q, i) * (32.0 / (2.0 * PI_F))))
    return phase5 & 0x1F

def get_synclock_dac_code(delta_phase8):
    delta_f = delta_phase8 * (20.0 / 256.0)
    if delta_f >= 0:
        val = 20.0 + 38.0 * math.tanh(delta_f / 2.2)
    else:
        gauss = math.exp(-((delta_f + 2.1) / 1.1) ** 2)
        g0 = math.exp(-((0.0 + 2.1) / 1.1) ** 2)
        dev = -20.0 * (gauss - g0) / (1.0 - g0)
        val = 20.0 + dev
    code = int(round(val))
    return max(0, min(63, code))

lut = [0] * 1024

for idx in range(1024):
    prev_ph = (idx >> 5) & 0x1F
    curr_ph = idx & 0x1F
    delta = s_phase5_centroid_phase8[curr_ph] - s_phase5_centroid_phase8[prev_ph]
    if delta >= 128:
        delta -= 256
    if delta < -128:
        delta += 256
    dac_code = get_synclock_dac_code(delta) # 6 bits: 0..63
    
    word = dac_code & 0x3F
    if idx < 256:
        ph5 = q4_phase5(idx)
        word |= (ph5 & 0x1F) << 8
    lut[idx] = word

bsasm_content = f"""# Golden Phase5 WBFM core with Resonance Sync-Lock Discriminator S-Curve
# 40 MS/s packed Q4/I4 input and 40 MS/s (20 MS/s 2-hold) six-bit CVBS output.
#
# LUT layout (16-bit words):
#   LUT[raw_q4_i4].bits[12:8] = uniform five-bit polar phase
#   LUT[(previous_phase << 5) | current_phase].bits[5:0] = 6-bit DAC code
#
# Sync-Lock resonance transfer function:
#   Pedestal: 20 (nominal blanking level)
#   True Sync Tip: drops to 0 at -2.1 MHz carrier deviation (delta_f = -2.1 MHz)
#   Out-of-band noise / pre-emphasis overshoot: bounded >= 14 (ABOVE monitor sync threshold 10)
#   Luminance: smooth tanh curve up to 57 without harsh clipping at 63

cfg prefetch true
cfg eof_on upstream
cfg trailing_bytes 9
cfg lut_width_bits 16

lut {" ".join(map(str, lut))}

address_phase:
    set 26..30 L,
    set 16 8,
    set 17 9,
    set 18 10,
    set 19 11,
    set 20 12,
    set 21 13,
    set 22 14,
    set 23 15,
    set 24..25 L,
    read 16

address_delta:
    set 26..30 L8..L12,
    set 16 L8,
    set 17 L9,
    set 18 L10,
    set 19 L11,
    set 20 L12,
    set 21 O26,
    set 22 O27,
    set 23 O28,
    set 24 O29,
    set 25 O30,
    set 31 L

emit:
    set 0..5 L0..L5,
    set 6..7 L,
    set 8..13 L0..L5,
    set 14..15 L,
    set 26..30 O26..O30,
    set 16 8,
    set 17 9,
    set 18 10,
    set 19 11,
    set 20 12,
    set 21 13,
    set 22 14,
    set 23 15,
    set 24..25 L,
    set 31 L,
    read 16,
    write 16,
    jmp address_delta
"""

target = ROOT / "main/c5vrx2_wbfm_q4_phase5_synclock_2to1.bsasm"
target.write_text(bsasm_content, encoding="utf-8")
print(f"Generated {target} successfully!")
