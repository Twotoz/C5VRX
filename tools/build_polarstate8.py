#!/usr/bin/env python3
"""Generate the first TX-only PolarState8 geometric-seed LUT.

This is a reproducible hardware experiment, not a trained or validated
improvement over Golden. Every current raw byte addresses the LUT. History is
compressed to two LUT bits plus the previous raw I-sign bit.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASM = ROOT / "main" / "fm_polarstate8.bsasm"
TAU = 2 * math.pi
CENTERS = (
    tuple(math.radians(deg) for deg in (-67.5, -22.5, 22.5, 67.5)),
    tuple(math.radians(deg) for deg in (112.5, 157.5, 202.5, 247.5)),
)


def phase(raw: int) -> float:
    # Same bucket-center convention as the existing trajectory generator.
    q = ((raw & 15) << 6) + 31.5
    i = (((raw >> 4) & 15) << 6) + 31.5
    if q >= 512:
        q -= 1024
    if i >= 512:
        i -= 1024
    return math.atan2(q, i)


def wrap(angle: float) -> float:
    return (angle + math.pi) % TAU - math.pi


def iround(value: float) -> int:
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


def cvbs(angle: float) -> int:
    # Existing P20/G2 calibration, with adjacent 25 ns gain (2x Golden).
    phase8 = iround(angle * 256 / TAU)
    n = phase8 * 3
    correction = -((-n + 2) // 4) if n < 0 else (n + 2) // 4
    return max(0, min(63, 20 + correction))


def state2(raw: int) -> int:
    anchor = (raw >> 7) & 1
    theta = phase(raw)
    return min(range(4), key=lambda k: abs(wrap(theta - CENTERS[anchor][k])))


def state3(raw: int) -> int:
    return state2(raw) | (((raw >> 7) & 1) << 2)


def word(state: int, raw: int) -> int:
    anchor = state >> 2
    current = phase(raw)
    previous = CENTERS[anchor][state & 3]
    # A fixed raw phasor reaches its own state after one sample, then must
    # produce the pedestal. Near-origin raw bytes are not reliable angles.
    i = ((raw >> 4) & 15) - (16 if raw & 0x80 else 0)
    q = (raw & 15) - (16 if raw & 0x08 else 0)
    if state == state3(raw) or i * i + q * q <= 2:
        dac = 20
    else:
        dac = cvbs(2 * wrap(current - previous))
    return dac | (state2(raw) << 6)


def generate() -> str:
    words = [word(state, raw) for state in range(8) for raw in range(256)]
    assert len(words) == 2048 and all(0 <= w <= 255 for w in words)
    header = """# PolarState8 geometric seed. Experimental, not a proven video demodulator.
# LUT8 O16..26 = current raw8 | (previous learned2 << 8) | (previous I-sign << 10).
# LUT word L0..5 = DAC6; L6..7 = next learned2. O27 retains raw bit7.
# One prime, then one TX-only read8/write8/jmp bundle per 25 ns sample.
cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 8
"""
    table = "\n".join("lut " + " ".join(map(str, words[i:i + 32]))
                      for i in range(0, 2048, 32))
    program = """
prime:
    set 16..23 0..7,
    set 24..26 L,
    set 27 7,
    read 8

stream:
    set 0..5 L0..L5,
    set 6..7 L,
    set 16..23 0..7,
    set 24..25 L6..L7,
    set 26 O27,
    set 27 7,
    read 8,
    write 8,
    jmp stream
"""
    return header + table + program


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = generate()
    if args.write:
        ASM.write_text(data, encoding="utf-8")
    if args.check:
        assert ASM.read_text(encoding="utf-8") == data, "regenerate with --write"
    print(f"PolarState8: {2048} LUT8 words, {len(data)} source bytes")


if __name__ == "__main__":
    main()
