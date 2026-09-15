#!/usr/bin/env python3
"""Generate the Phase5 Adjacent FM BitScrambler assembly.

Same q4_2to1 instruction structure (reads both bytes per 16-bit input,
adjacent delta via ADDCTIAL, pair-sum, bank 1 DAC mapping), but:
  - Bank 0: Phase5 centroid-quantized phase8 instead of raw atan2 phase8
  - Bank 1: same calibrated sum→DAC mapping (pedestal 20, gain 2)
  - [D,D] output: writes 16 bits per cycle (duplicate 6-bit DAC code)
  - State moved from O8..O15 to O16..O23 across emit/address1 boundary

The LUT is embedded in the .bsasm so the PARLIO TX decorator retains it.
"""
import math
from pathlib import Path

PI = math.pi

# Phase5 centroid phase8 values (from wbfm_q4.c s_phase5_centroid_phase8)
CENTROID_PHASE8 = [
       0,    8,   15,   24,   32,   40,   49,   56,
      64,   72,   79,   87,   96,  104,  113,  120,
    -128, -120, -113, -104,  -96,  -88,  -79,  -72,
     -64,  -56,  -49,  -40,  -32,  -23,  -15,   -8,
]


def signed_bucket_center(code: int, bits: int) -> float:
    width = 1 << (10 - bits)
    center = code * width + (width - 1) * 0.5
    if center >= 512:
        center -= 1024
    return center


def q4_phase5(packed: int) -> int:
    q = signed_bucket_center(packed & 0x0F, 4)
    i = signed_bucket_center(packed >> 4, 4)
    p5 = round(math.atan2(q, i) * (32 / (2 * PI)))
    return p5 & 0x1F


def scale_real_sum(value: int, gain: int = 2) -> int:
    numerator = value * (gain + 1)
    if numerator < 0:
        return -((-numerator + 2) // 4)
    else:
        return (numerator + 2) // 4


def build_phase5_adj_lut(pedestal: int = 20, gain: int = 2) -> list[int]:
    lut = [0] * 1024
    for index in range(1024):
        if index < 0x100:
            p5 = q4_phase5(index)
            phase_signed = CENTROID_PHASE8[p5]
            # Polarity: CURRENT_MINUS_PREVIOUS (default)
            phase_mod = phase_signed & 0xFF
            negative_phase = (-phase_signed) & 0xFF
            lut[index] = phase_mod | (negative_phase << 8)
        elif index < 0x200:
            s = index & 0xFF
            if s >= 128:
                s -= 256
            code = pedestal + scale_real_sum(s, gain)
            code = max(0, min(63, code))
            lut[index] = code
        else:
            lut[index] = 0
    return lut


def format_lut_line(lut: list[int]) -> str:
    return " ".join(str(v) for v in lut)


def generate_bsasm(lut: list[int]) -> str:
    lut_str = format_lut_line(lut)
    return f"""\
# Phase5 Adjacent FM demodulator: centroid-quantised adjacent-delta pair-sum.
#
# Same q4_2to1 ALU pipeline but with Phase5-centroid phase8 values in the LUT
# instead of raw atan2 phase8. Both bytes of each 16-bit input pair are used
# (adjacent FM at 25 ns steps), not just odd (endpoint FM at 50 ns steps).
#
#   A = Golden Phase5: phase5[n+2] - phase5[n]   (skips middle sample)
#   B = This:   wrap(centroid8[n+1]-centroid8[n]) + wrap(centroid8[n+2]-centroid8[n+1])
#
# Bank 0 (0x000-0x0FF): LUT[packed_byte] = centroid_phase8 | (-centroid_phase8 << 8)
# Bank 1 (0x100-0x1FF): LUT[0x100|sum_mod8] = 6-bit DAC code (pedestal 20, gain 2)
#
# Output is [D,D] at 40 MS/s: both bytes of each 16-bit TX word carry the same
# 6-bit DAC code, so the physical DAC rate is 40 MS/s while info rate is 20 MS/s.
#
# State (negative phase of last sample) is carried in O8..O15 during processing,
# then saved to O16..O23 across the write/address boundary.

cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 16

lut {lut_str}

address1:
    # Restore state from O16..O23 (where emit saved it) into O8..O15 for the
    # processing pipeline. Address the first byte of the input pair.
    # Reset accumulator here once per pair.
    set 8..15 O16..O23,
    set 16 0,
    set 17 1,
    set 18 2,
    set 19 3,
    set 20 4,
    set 21 5,
    set 22 6,
    set 23 7,
    set 24..25 L,
    read 8,
    LDCTDAL 0

add_current1:
    set 0..7 L8..L15,
    set 8..15 O8..O15,
    set 16..23 L0..L7,
    ADDCTIAL

add_previous1:
    set 0..7 O0..O7,
    set 16..23 O8..O15,
    ADDCTIAL

address2:
    # Sample 1 is now the previous sample. Store its neg phase in O8..O15.
    # Address sample 2 and prefetch next pair's first byte.
    set 8..15 O0..O7,
    set 16 0,
    set 17 1,
    set 18 2,
    set 19 3,
    set 20 4,
    set 21 5,
    set 22 6,
    set 23 7,
    set 24..25 L,
    read 8

add_current2:
    set 0..7 L8..L15,
    set 8..15 O8..O15,
    set 16..23 L0..L7,
    ADDCTIAL

add_previous2:
    set 0..7 O0..O7,
    set 16..23 O8..O15,
    ADDCTIAL

map_cvbs:
    # Select LUT bank 1 (indices 0x100..0x1ff) with the low accumulator byte.
    # Save sample 2's neg phase in O8..O15 for emit to preserve.
    set 8..15 O0..O7,
    set 16 A0,
    set 17 A1,
    set 18 A2,
    set 19 A3,
    set 20 A4,
    set 21 A5,
    set 22 A6,
    set 23 A7,
    set 24 H,
    set 25 L

emit:
    # [D,D]: duplicate 6-bit DAC code in both bytes of the 16-bit output word.
    # Save state (neg_phase of last sample) from O8..O15 to O16..O23 so it
    # survives the 16-bit write and can be restored by address1.
    set 0..5 L0..L5,
    set 6..7 L,
    set 8..13 L0..L5,
    set 14..15 L,
    set 16..23 O8..O15,
    write 16,
    jmp address1
"""


def main():
    lut = build_phase5_adj_lut()

    # Verify LUT sanity
    non_zero_phases = sum(1 for i in range(256) if (lut[i] & 0xFF) != 0)
    print(f"Bank 0: {non_zero_phases}/256 entries have non-zero phase")

    dac_codes = [lut[0x100 + i] for i in range(256)]
    print(f"Bank 1: DAC range [{min(dac_codes)}..{max(dac_codes)}]")

    # Verify centroid quantization
    for packed in [0x00, 0x55, 0xAA, 0xFF, 0x11, 0x37]:
        p5 = q4_phase5(packed)
        centroid = CENTROID_PHASE8[p5]
        raw_q = signed_bucket_center(packed & 0x0F, 4)
        raw_i = signed_bucket_center(packed >> 4, 4)
        raw_phase8 = round(math.atan2(raw_q, raw_i) * (256 / (2 * PI)))
        raw_phase8 = max(-128, min(127, raw_phase8))
        print(f"  packed=0x{packed:02x}: raw_phase8={raw_phase8:+4d}, "
              f"phase5={p5:2d}, centroid_phase8={centroid:+4d}, "
              f"delta={centroid - raw_phase8:+3d}")

    bsasm = generate_bsasm(lut)
    out = Path(__file__).resolve().parent.parent / "main" / "c5vrx2_wbfm_q4_phase5_adj_2to1.bsasm"
    out.write_text(bsasm, encoding="utf-8", newline="\n")
    print(f"\nGenerated: {out}")
    print(f"LUT entries: {len(lut)}, embedded in .bsasm")

    # Count instructions
    lines = bsasm.strip().split("\n")
    labels = [l for l in lines if l and l[0].isalpha() and l.rstrip().endswith(":")]
    print(f"Instructions (labels): {len(labels)} (max 8)")


if __name__ == "__main__":
    main()
