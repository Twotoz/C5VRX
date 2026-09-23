#!/usr/bin/env python3
"""Exhaustive oracle for the single-core two-pass M2M exact-adjacent pipeline.

Proves:
  raw Q4/I4 -> production Phase5 (pass 1)
  Phase5 triplet -> exact adjacent LIFT DAC (pass 2)

over:
  previous Phase5 (32) x middle raw byte (256) x current raw byte (256)
= 2,097,152 live-boundary combinations.

This is a mathematical/embedded-LUT proof. Realtime M2M throughput and finite
block continuity remain hardware gates and are reported by firmware telemetry.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main"


def lut_values(path: Path) -> list[int]:
    text = path.read_text(encoding="utf-8")
    values: list[int] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("lut "):
            values.extend(int(x, 0) for x in stripped[4:].replace(",", " ").split())
    return values


def wrap32(delta: int) -> int:
    return ((delta + 16) & 31) - 16


def oracle(previous: int, middle: int, current: int) -> int:
    pair = wrap32(middle - previous) + wrap32(current - middle)
    return max(0, min(63, 20 + 6 * pair))


def stage1_address(previous: int, middle: int, current: int) -> int:
    return ((previous >> 2) & 0x7) | (((middle >> 1) & 0xF) << 3) | (((current >> 2) & 0x7) << 7)


def stage2_address(previous: int, middle: int, current: int, token: int) -> int:
    free = (previous & 0x3) | ((middle & 0x1) << 2) | ((current & 0x3) << 3)
    return free | ((token & 0x1F) << 5)


def self_test() -> None:
    phase_lut = lut_values(MAIN / "fm_m2m_phase.bsasm")
    lift_lut = lut_values(MAIN / "fm_m2m_lift.bsasm")
    if len(phase_lut) < 256:
        raise AssertionError(f"phase LUT too short: {len(phase_lut)}")
    if len(lift_lut) != 1024:
        raise AssertionError(f"LIFT LUT must contain 1024 words, got {len(lift_lut)}")

    # Main production Golden Phase5 table is embedded in fm.bsasm. Its first
    # 256 LUT words carry Phase5 in bits 8..12.
    golden_lut = lut_values(MAIN / "fm.bsasm")
    if len(golden_lut) < 256:
        raise AssertionError("production fm.bsasm LUT too short")

    if phase_lut != lift_lut:
        raise AssertionError("M2M pass programs must embed one identical resident LUT")

    def sparse_phase(word: int) -> int:
        return (
            (((word >> 6) & 1) << 0)
            | (((word >> 7) & 1) << 1)
            | (((word >> 13) & 1) << 2)
            | (((word >> 14) & 1) << 3)
            | (((word >> 15) & 1) << 4)
        )

    phase = [sparse_phase(phase_lut[x]) for x in range(256)]
    production = [(golden_lut[x] >> 8) & 0x1F for x in range(256)]
    for raw in range(256):
        if phase[raw] != production[raw]:
            raise AssertionError(
                f"pass1 Phase5 mismatch raw=0x{raw:02x}: "
                f"m2m={phase[raw]} production={production[raw]}"
            )

    checked = 0
    tokens: set[int] = set()
    for previous in range(32):
        for middle_raw in range(256):
            middle = phase[middle_raw]
            for current_raw in range(256):
                current = phase[current_raw]
                a1 = stage1_address(previous, middle, current)
                word1 = lift_lut[a1]
                token = (word1 >> 8) & 0x1F
                tokens.add(token)
                a2 = stage2_address(previous, middle, current, token)
                got = lift_lut[a2] & 0x3F
                want = oracle(previous, middle, current)
                if got != want:
                    raise AssertionError(
                        f"M2M LIFT mismatch p={previous} "
                        f"middle_raw=0x{middle_raw:02x} current_raw=0x{current_raw:02x}: "
                        f"got={got} want={want} a1={a1} token={token} a2={a2}"
                    )
                checked += 1

    expected = 32 * 256 * 256
    if checked != expected:
        raise AssertionError(f"expected {expected}, checked {checked}")

    print(
        "M2M exact-adjacent oracle passed: "
        f"{checked}/{expected} raw-boundary combinations exact; "
        f"{len(tokens)} stage-1 tokens used; one shared LUT image"
    )


if __name__ == "__main__":
    self_test()
