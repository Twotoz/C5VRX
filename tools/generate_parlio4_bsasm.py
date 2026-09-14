import math
from pathlib import Path

ROOT = Path("C:/Users/leonb/Twotoz/C5VRX-issue11-output")

PI_F = 3.14159265358979323846

# Grouped DAC levels on 0..63 scale
GROUPED_LEVELS = [0, 4, 8, 12, 17, 21, 25, 29, 34, 38, 42, 46, 51, 55, 59, 63]

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

def phase5_delta_phase8(previous, current):
    delta = s_phase5_centroid_phase8[current] - s_phase5_centroid_phase8[previous]
    if delta >= 128:
        delta -= 256
    if delta < -128:
        delta += 256
    return delta

def scale_real_sum(sum_val, calibration_gain=2):
    # default discriminator_gain = 2
    numerator = sum_val * (calibration_gain + 1)
    if numerator < 0:
        return -((-numerator + 2) // 4)
    else:
        return (numerator + 2) // 4

def quantize_to_grouped_dac(d_code):
    d_clamped = max(0, min(63, d_code))
    best_c = 0
    best_diff = 999
    for c, lvl in enumerate(GROUPED_LEVELS):
        diff = abs(lvl - d_clamped)
        if diff < best_diff:
            best_diff = diff
            best_c = c
    return best_c

lut = [0] * 1024

# Build the 1024-word dual-purpose LUT
for idx in range(1024):
    prev_ph = (idx >> 5) & 0x1F
    curr_ph = idx & 0x1F
    delta = phase5_delta_phase8(prev_ph, curr_ph)
    d_code = 20 + scale_real_sum(delta, calibration_gain=2)
    c_code = quantize_to_grouped_dac(d_code) # 4 bits: 0..15
    
    word = c_code & 0x0F
    if idx < 256:
        ph5 = q4_phase5(idx)
        word |= (ph5 & 0x1F) << 8
    lut[idx] = word

bsasm_content = f"""# 4-bit PARLIO @ 80 MHz steady-state quality WBFM core
# 40 MS/s packed Q4/I4 input and 80 MS/s four-bit grouped DAC CVBS output.
#
# LUT layout (16-bit words):
#   LUT[raw_q4_i4].bits[12:8] = uniform five-bit polar phase
#   LUT[(previous_phase << 5) | current_phase].bits[3:0] = 4-bit grouped DAC code
#
# Grouped DAC mapping (16 levels on 0..63 scale):
#   Bits 0..3 map to:
#     Bit 0: GPIO 11 (weight 4)
#     Bit 1: GPIO 12 (weight 8)
#     Bit 2: GPIO 8 + GPIO 23 (weight 17)
#     Bit 3: GPIO 9 + GPIO 24 (weight 34)
#
# In emit:
#   Emits four 4-bit samples per 16-bit word (write 16)
#   At 80 MHz, four nibbles take exactly 50 ns (matching 2 input bytes @ 40 MB/s).

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
    # Emit four 4-bit grouped DAC samples at 80 MS/s:
    # Sample 0 (bits 0..3), Sample 1 (bits 4..7),
    # Sample 2 (bits 8..11), Sample 3 (bits 12..15).
    # Total hold: 50 ns across four 12.5 ns cycles.
    set 0..3 L0..L3,
    set 4..7 L0..L3,
    set 8..11 L0..L3,
    set 12..15 L0..L3,
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

target = ROOT / "main/c5vrx2_wbfm_q4_parlio4_80m_2to1.bsasm"
target.write_text(bsasm_content, encoding="utf-8")
print(f"Generated {target} successfully!")
