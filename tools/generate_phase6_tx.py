#!/usr/bin/env python3
"""Generate the exact raw-IQ, TX-only Phase6 BitScrambler program.

Each 50 ns output uses the middle and current 25 ns samples. Four TX bundles
replace the two TX bundles plus the 40 MS/s RX preprocessing channel in PR66.
The DMA ring stays raw Q4/I4, so the production ARC observer remains valid.
"""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BSASM = ROOT / "main" / "fm_phase6_tx.bsasm"


def wrap32(value: int) -> int:
    return ((value + 16) & 31) - 16


def selected_delta(previous: int, middle: int, current: int) -> int:
    """Keep coherent large winding; otherwise retain Golden's noise immunity."""
    first = wrap32(middle - previous)
    second = wrap32(current - middle)
    unwrapped = first + second
    if (abs(first) <= 12 and abs(second) <= 12
            and abs(first - second) <= 6 and abs(unwrapped) >= 16):
        return unwrapped
    return wrap32(current - previous)


def dac(previous: int, middle: int, current: int) -> int:
    delta = selected_delta(previous, middle, current)
    return max(0, min(63, 20 + 6 * delta))


def stage1_address(previous: int, middle: int, current: int) -> int:
    return (previous >> 2) | ((middle >> 1) << 3) | ((current >> 2) << 7)


def stage2_address(previous: int, middle: int, current: int, token: int) -> int:
    return (previous & 3) | ((middle & 1) << 2) | ((current & 3) << 3) | (token << 5)


def phase5_from_golden() -> list[int]:
    line = next(line for line in (ROOT / "main" / "fm.bsasm").read_text().splitlines()
                if line.startswith("lut "))
    words = [int(item) for item in line.split()[1:]]
    assert len(words) >= 256
    return [(words[raw] >> 8) & 31 for raw in range(256)]


def generate_lut() -> tuple[list[int], list[int], list[int], list[int]]:
    phase = phase5_from_golden()
    vectors: dict[tuple[int, ...], int] = {}
    stage1 = [0] * 1024
    for p_hi in range(8):
        for m_hi in range(16):
            for c_hi in range(8):
                vector = tuple(
                    dac((p_hi << 2) | p_lo, (m_hi << 1) | m_lo, (c_hi << 2) | c_lo)
                    for p_lo in range(4) for m_lo in range(2) for c_lo in range(4)
                )
                token = vectors.setdefault(vector, len(vectors))
                stage1[p_hi | (m_hi << 3) | (c_hi << 7)] = token
    assert len(vectors) == 25, len(vectors)

    stage2 = [20] * 1024
    for vector, token in vectors.items():
        for p_lo in range(4):
            for m_lo in range(2):
                for c_lo in range(4):
                    address = p_lo | (m_lo << 2) | (c_lo << 3) | (token << 5)
                    stage2[address] = vector[(p_lo << 3) | (m_lo << 2) | c_lo]

    # 6 DAC + 5 token + 5 raw-phase bits fill exactly one physical LUT16 word.
    unified = [
        stage2[i] | (stage1[i] << 6) | ((phase[i] if i < 256 else 0) << 11)
        for i in range(1024)
    ]
    for p in range(32):
        for m in range(32):
            for c in range(32):
                token = (unified[stage1_address(p, m, c)] >> 6) & 31
                code = unified[stage2_address(p, m, c, token)] & 63
                assert code == dac(p, m, c), (p, m, c, code)
    for raw in range(256):
        assert (unified[raw] >> 11) == phase[raw]
    assert selected_delta(0, 10, 20) == 20  # +225 degrees, not -135.
    assert selected_delta(20, 10, 0) == -20
    for p in range(32):
        for c in range(32):
            if abs(wrap32(c - p)) <= 7:
                for m in range(32):
                    assert selected_delta(p, m, c) == wrap32(c - p)
    return phase, stage1, stage2, unified


def render(unified: list[int]) -> str:
    lut = "lut " + " ".join(str(word) for word in unified)
    return f"""# Raw-Q4/I4 TX-only Phase6. Input: 40 MB/s raw ring; output: [D,D] at 40 MHz.
# Four bundles per 50 ns output, no RX BitScrambler or ring format change.
# LUT16: L0..5 DAC, L6..10 stage-1 token, L11..15 raw8->Phase5.
# Stage1 p[4:2],m[4:1],c[4:2] -> token; stage2 p[1:0],m[0],c[1:0],token -> DAC.
# Coherent large adjacent motion is added without a second wrap; noisy or
# ordinary motion uses Golden's endpoint delta.
cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 16
{lut}

prime_previous:
    # Match Golden alignment: second raw sample is previous endpoint p.
    set 16..23 8..15,
    read 16

prime_middle:
    # After the read, low byte is m; high byte is c.
    set 26..30 L11..L15,
    set 16..23 0..7,
    jmp current_phase

current_phase:
    # L is phase(m). Preserve p and m while looking up phase(c).
    set 0..4 O26..O30,
    set 5..9 L11..L15,
    set 16..23 8..15

stage1:
    # p,m in O; phase(c) in L. Keep all three states for stage 2.
    set 0..9 O0..O9,
    set 10..14 L11..L15,
    set 16..18 O2..O4,
    set 19..22 O6..O9,
    set 23..25 L13..L15

stage2:
    # Save c as next p, and address the exact 10-bit residual lookup.
    set 26..30 O10..O14,
    set 16..17 O0..O1,
    set 18 O5,
    set 19..20 O10..O11,
    set 21..25 L6..L10

emit:
    # Output [D,D]. M[23:16] already holds the following pair's middle.
    # read16 advances that pair into M[15:0] after the lookup address is set.
    set 0..5 L0..L5,
    set 6..7 L,
    set 8..13 L0..L5,
    set 14..15 L,
    set 16..23 16..23,
    set 26..30 O26..O30,
    read 16,
    write 16,
    jmp current_phase
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    phase, stage1, stage2, unified = generate_lut()
    output = render(unified)
    if args.write:
        BSASM.write_text(output, encoding="utf-8")
    else:
        assert BSASM.read_text(encoding="utf-8") == output
    print("Phase6 TX LUT: 256 raw phases, 32768 exact policy results, 25 tokens, 1024x16 words")


if __name__ == "__main__":
    main()
