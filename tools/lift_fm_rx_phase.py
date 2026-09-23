#!/usr/bin/env python3
"""Generate the 40-MS/s RX BitScrambler Phase5 preprocessor."""

from __future__ import annotations

import argparse
from pathlib import Path

from train_trajectory_v2 import phase5

ROOT = Path(__file__).resolve().parents[1]
BSASM = ROOT / "main" / "fm_rx_phase.bsasm"


def signed4(n: int) -> int:
    n &= 0xF
    return n - 16 if n & 8 else n


def amplitude_class(raw: int) -> int:
    q = signed4(raw)
    i = signed4(raw >> 4)
    power = i * i + q * q

    # Preserve the two most important ARC hazards explicitly:
    # class 0 = near-origin; class 7 = rail/high-power overload.
    if i in (-8, 7) or q in (-8, 7):
        return 7
    if power <= 4:
        return 0
    if power <= 7:
        return 1
    if power <= 15:
        return 2
    if power <= 27:
        return 3
    if power <= 34:
        return 4
    if power <= 45:
        return 5
    if power <= 60:
        return 6
    return 7


def ring_code(raw: int) -> int:
    return phase5(raw) | (amplitude_class(raw) << 5)


def build_lut() -> list[int]:
    lut = [0] * 1024
    for raw in range(256):
        lut[raw] = ring_code(raw)
    return lut


def render(lut: list[int]) -> str:
    return """# LIFT-FM RX preprocessor: full raw Q4/I4 -> Phase5 + amplitude class.
#
# Input : one packed Q4/I4 byte per 25 ns (40 MS/s).
# Output: one byte per input sample, same 40 MB/s ring bandwidth.
#
#   bits 0..4 = exact production Phase5 state
#   bits 5..7 = coarse raw-power / origin / overload class
#
# The TX LIFT-FM backend consumes only bits 0..4. The upper three bits keep
# enough raw-envelope information for the slow ARC supervisor without a
# second DMA stream.
#
# Steady state is one BitScrambler bundle per input byte. RX and TX use
# independent C5 BitScrambler channels and independent instruction/LUT RAM.
#
# Use the prefetched low byte lane for the LUT address. The ESP32-C5 mux cannot
# select L (the previous LUT result) and M[63:56] in one instruction, so the
# high-lane explicit-read form cannot sustain one output byte per input byte.
cfg prefetch true
cfg eof_on upstream
cfg trailing_bytes 0
cfg lut_width_bits 16
lut %s

prime:
    # Prefetch presents the first real RX byte in M[7:0]. Address that sample
    # while advancing the input stream by one byte.
    set 16..23 0..7,
    set 24..25 L,
    read 8

convert:
    # Emit the previous sample's LUT result while addressing the prefetched
    # next byte from the mux-compatible low register lane.
    set 0..7 L0..L7,
    set 16..23 0..7,
    set 24..25 L,
    read 8,
    write 8,
    jmp convert
""" % " ".join(str(v) for v in lut)


def self_test(check_generated: bool = True) -> None:
    lut = build_lut()
    for raw in range(256):
        code = lut[raw]
        if (code & 31) != phase5(raw):
            raise AssertionError(f"phase mismatch raw=0x{raw:02x}")
        if (code >> 5) != amplitude_class(raw):
            raise AssertionError(f"class mismatch raw=0x{raw:02x}")
    if max(lut) > 255:
        raise AssertionError("RX ring code must remain one byte")
    if check_generated:
        expected = render(lut)
        if not BSASM.exists() or BSASM.read_text(encoding="utf-8") != expected:
            raise AssertionError(
                "generated RX Phase5 preprocessor drift; run "
                "python3 tools/lift_fm_rx_phase.py --write --self-test"
            )
    print(
        "LIFT-FM RX Phase5 self-test passed: 256/256 raw Q4 states exact, "
        "8-bit in -> 8-bit out, 40-MS/s one-bundle steady state"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.write:
        lut = build_lut()
        BSASM.write_text(render(lut), encoding="utf-8")
        print(f"wrote {BSASM.relative_to(ROOT)}")
    if args.self_test or not args.write:
        self_test(check_generated=True)


if __name__ == "__main__":
    main()
