#!/usr/bin/env python3
"""Generate and prove the exact 1024x16 LIFT-FM phase-domain oracle.

This is the LUT16 follow-up to tools/lift_fm_synth.py.

The exact Phase5 function

    (p, m, c) -> clamp(20 + 6*(wrap32(m-p)+wrap32(c-m)), 0, 63)

has a 10-bit -> 5-bit -> 10-bit functional decomposition:

    stage 1 address:
        p[4:2]  (3)
        m[4:1]  (4)
        c[4:2]  (3)
                 --
                 10

    stage 1 output:
        token[4:0]  (only 24 of 32 values are used)

    stage 2 address:
        p[1:0]      (2)
        m[0]        (1)
        c[1:0]      (2)
        token[4:0]  (5)
                     --
                     10

A single physical 1024x16 LUT serves both stages at once:
  - bits 0..5:  stage-2 exact DAC result
  - bits 8..12: stage-1 token

The generated BitScrambler program is a phase-domain hardware oracle. Its test
stream contains Phase5 symbols already; it is NOT yet the live raw-Q4 decoder.
The steady-state schedule is exactly two bundles per 20 MS/s output:

    lift -> emit -> lift -> emit ...

This file is intentionally dependency-free and exhaustively checks all 32768
Phase5 triplets before writing the .bsasm source.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from lift_fm_synth import oracle

ROOT = Path(__file__).resolve().parents[1]
BSASM = ROOT / "main" / "fm_lift16_phase.bsasm"

TOKEN_COUNT = 24
LUT_WORDS = 1024


def stage1_address(previous: int, middle: int, current: int) -> int:
    return (
        ((previous >> 2) & 0x07)
        | (((middle >> 1) & 0x0F) << 3)
        | (((current >> 2) & 0x07) << 7)
    )


def stage2_address(previous: int, middle: int, current: int, token: int) -> int:
    return (
        (previous & 0x03)
        | ((middle & 0x01) << 2)
        | ((current & 0x03) << 3)
        | ((token & 0x1F) << 5)
    )


def build_tables() -> tuple[list[int], list[int], list[int]]:
    vectors: dict[tuple[int, ...], int] = {}
    by_token: list[tuple[int, ...]] = []
    stage1 = [0] * LUT_WORDS

    for p_hi in range(8):
        for m_hi in range(16):
            for c_hi in range(8):
                vector = tuple(
                    oracle((p_hi << 2) | p_lo,
                           (m_hi << 1) | m0,
                           (c_hi << 2) | c_lo)
                    for p_lo in range(4)
                    for m0 in range(2)
                    for c_lo in range(4)
                )
                token = vectors.get(vector)
                if token is None:
                    token = len(vectors)
                    vectors[vector] = token
                    by_token.append(vector)
                stage1[p_hi | (m_hi << 3) | (c_hi << 7)] = token

    if len(vectors) != TOKEN_COUNT:
        raise AssertionError(
            f"LUT16 decomposition drift: expected {TOKEN_COUNT} tokens, "
            f"got {len(vectors)}"
        )

    stage2 = [20] * LUT_WORDS
    for token, vector in enumerate(by_token):
        for p_lo in range(4):
            for m0 in range(2):
                for c_lo in range(4):
                    free_index = (p_lo << 3) | (m0 << 2) | c_lo
                    address = (
                        p_lo | (m0 << 2) | (c_lo << 3) | (token << 5)
                    )
                    stage2[address] = vector[free_index]

    unified = [
        ((stage1[address] & 0x1F) << 8) | (stage2[address] & 0x3F)
        for address in range(LUT_WORDS)
    ]
    return stage1, stage2, unified


def exhaustive_verify(stage1: list[int], stage2: list[int]) -> None:
    checked = 0
    for previous in range(32):
        for middle in range(32):
            for current in range(32):
                token = stage1[stage1_address(previous, middle, current)]
                got = stage2[
                    stage2_address(previous, middle, current, token)
                ]
                want = oracle(previous, middle, current)
                if got != want:
                    raise AssertionError(
                        "LUT16 mismatch "
                        f"p={previous} m={middle} c={current}: "
                        f"token={token} got={got} want={want}"
                    )
                checked += 1
    if checked != 32768:
        raise AssertionError(f"expected 32768 triplets, checked {checked}")


def render_bsasm(unified: list[int]) -> str:
    lut_line = "lut " + " ".join(str(word) for word in unified)
    return f"""# LIFT-FM LUT16 exact Phase5 hardware oracle.
#
# IMPORTANT: this program consumes synthetic/predecoded Phase5 test symbols.
# It is not selectable as the live raw-Q4 flight demodulator yet.
#
# Test input format after the one-time prime:
#   low byte  bits 0..4  = middle Phase5
#   high byte bits 8..12 = current Phase5
#
# O26..O30 stores previous/current endpoint Phase5 across pairs.
# O0..O4 temporarily stores the five free bits needed by stage 2.
#
# Shared 1024x16 LUT:
#   L0..L5   = stage-2 exact P20/G2 DAC
#   L8..L12  = stage-1 5-bit token (24 states used)
#
# Steady-state schedule is exactly two bundles per output:
#   lift -> emit -> lift -> emit ...
#
# Stage 1 address (10 bits):
#   p[4:2], m[4:1], c[4:2]
#
# Stage 2 address (10 bits):
#   p[1:0], m[0], c[1:0], token[4:0]
#
# No second wrap is performed.
cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 16
{lut_line}

prime_previous:
    # Take the first endpoint Phase5 from the high byte of the prefetched
    # synthetic stream, then advance to the first [middle,current] pair.
    set 26..30 8..12,
    read 16

prime_stage1:
    # Address exact stage 1 for the first pair and save its five stage-2 free
    # bits. The old O26..O30 value is previous p; the new value becomes c.
    set 0 O26,
    set 1 O27,
    set 2 0,
    set 3 8,
    set 4 9,
    set 16..18 O28..O30,
    set 19..22 1..4,
    set 23..25 10..12,
    set 26..30 8..12,
    read 16

lift:
    # Stage-1 result is in L8..L12. Build the exact stage-2 address from the
    # preserved free bits plus the 5-bit token. Keep c as next pair's p.
    set 0..4 O0..O4,
    set 16..20 O0..O4,
    set 21..25 L8..L12,
    set 26..30 O26..O30

emit:
    # Stage-2 result is the exact adjacent-FM P20/G2 DAC code. Emit [D,D] and
    # simultaneously issue stage 1 for the following synthetic phase pair.
    set 0..5 L0..L5,
    set 6..7 L,
    set 8..13 L0..L5,
    set 14..15 L,
    set 16..18 O28..O30,
    set 19..22 1..4,
    set 23..25 10..12,
    set 0 O26,
    set 1 O27,
    set 2 0,
    set 3 8,
    set 4 9,
    set 26..30 8..12,
    read 16,
    write 16,
    jmp lift
"""


def write_generated() -> None:
    stage1, stage2, unified = build_tables()
    exhaustive_verify(stage1, stage2)
    BSASM.write_text(render_bsasm(unified), encoding="utf-8")
    print(f"wrote {BSASM.relative_to(ROOT)}")


def self_test(check_generated: bool = True) -> None:
    stage1, stage2, unified = build_tables()
    exhaustive_verify(stage1, stage2)
    if max(stage1) >= 32:
        raise AssertionError("stage-1 token exceeds five bits")
    if check_generated:
        expected = render_bsasm(unified)
        if not BSASM.exists() or BSASM.read_text(encoding="utf-8") != expected:
            raise AssertionError(
                "generated LUT16 oracle drift; run "
                "python3 tools/lift_fm_lut16.py --write --self-test"
            )
    print(
        "LIFT-FM LUT16 self-test passed: "
        "32768/32768 exact triplets, 10->5->10, "
        f"{TOKEN_COUNT} tokens, {LUT_WORDS}x16 shared LUT"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.write:
        write_generated()
    if args.self_test or not args.write:
        self_test(check_generated=True)


if __name__ == "__main__":
    main()
