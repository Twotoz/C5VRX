#!/usr/bin/env python3
"""Generate the 40-MS/s RX BitScrambler Phase5+ preprocessor."""

from __future__ import annotations

import argparse
from pathlib import Path

from train_trajectory_v2 import phase5

ROOT = Path(__file__).resolve().parents[1]
BSASM = ROOT / "main" / "fm_rx_phase5plus.bsasm"


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
    return """# Phase5+ RX preprocessor: full raw Q4/I4 -> Phase5 + amplitude class.
#
# Input : one packed Q4/I4 byte per 25 ns (40 MS/s).
# Output: one byte per input sample, same 40 MB/s ring bandwidth.
#
#   bits 0..4 = exact production Phase5 state
#   bits 5..7 = coarse raw-power / origin / overload class
#
# The TX Phase5+ backend consumes only bits 0..4. The upper three bits keep
# enough raw-envelope information for the slow ARC supervisor without a
# second DMA stream.
#
# Steady state is one BitScrambler bundle per input byte. RX and TX use
# independent C5 BitScrambler channels and independent instruction/LUT RAM.
#
# Do not use hardware prefetch on RX. bitscrambler_start() runs before PARLIO
# begins producing bytes, while ESP-IDF requires prefetch=true to synchronously
# obtain 64 input bits at startup. Instead, warm the 64-bit input register with
# eight ordinary read-8 cycles, then use the mux-compatible low byte lane in
# the one-bundle steady-state loop.
cfg prefetch false
cfg eof_on upstream
cfg trailing_bytes 0
cfg lut_width_bits 16
lut %s

prime_first:
    # Counter A starts the bounded eight-byte software prefill. Ordinary reads
    # may stall until PARLIO supplies data; unlike hardware prefetch this does
    # not require 64 bits to exist at bitscrambler_start().
    LDCTDA 0,
    read 8

fill_input:
    # prime_first read byte 1. This bundle executes seven times total:
    # six taken loops plus the final fall-through at A==6, reading bytes 2..8.
    # After that, the oldest sample is exactly in M[7:0].
    LOOPA 6 1 fill_input,
    read 8

prime_lookup:
    # Address sample 1 while advancing the input register to sample 2.
    set 16..23 0..7,
    set 24..25 L,
    read 8

convert:
    # Emit the previous sample's LUT result while addressing the next oldest
    # byte from M[7:0]. Steady state remains one bundle per 25 ns sample.
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
        "Phase5+ RX self-test passed: 256/256 raw Q4 states exact, "
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
