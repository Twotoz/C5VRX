#!/usr/bin/env python3
"""Generate and prove the guarded Phase5+ two-bundle phase-domain oracle.

The 50 ns endpoint delta is corrected only for coherent middle-sample
winding. The corrected phase difference is mapped to the DAC once, with
Golden-like suppression of incoherent near-half-turn endpoints. The resulting
32^3 Phase5 oracle has a 10-bit -> 5-bit -> 10-bit decomposition:

    stage 1 address:
        p[4:2]  (3)
        m[4:1]  (4)
        c[4:2]  (3)
                 --
                 10

    stage 1 output:
        token[4:0]  (30 of 32 values are used)

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

The generated BitScrambler program is the guarded Phase5+ TX backend.
Its input must already contain Phase5 symbols; the live dual-channel path feeds
it from the independent RX BitScrambler raw-Q4 preprocessor.
The steady-state schedule is exactly two bundles per 20 MS/s output:

    lift -> emit -> lift -> emit ...

This file is intentionally dependency-free and exhaustively checks all 32768
Phase5 triplets before writing the .bsasm source.
"""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BSASM = ROOT / "main" / "fm_phase5plus.bsasm"

TOKEN_COUNT = 30
LUT_WORDS = 1024


def wrap32(value: int) -> int:
    return ((value + 16) & 31) - 16


def oracle(previous: int, middle: int, current: int) -> int:
    """Use middle only for a coherent winding decision, then clip once.

    An isolated middle-sample phase glitch must collapse to the Golden
    endpoint result.  A smooth 0,10,20 trajectory must retain +20, not -12.
    """
    d0 = wrap32(middle - previous)
    d1 = wrap32(current - middle)
    endpoint = wrap32(current - previous)
    pair = d0 + d1
    coherent = (abs(d0) <= 12 and abs(d1) <= 12 and
                abs(d0 - d1) <= 6 and abs(pair) >= 16)
    if coherent:
        return max(0, min(63, 20 + 6 * pair))
    # Golden suppresses incoherent near-half-turn endpoints instead of
    # splatting them on a DAC rail. This p-independent approximation preserves
    # that safety curve within the 5-bit interstage token budget.
    if abs(endpoint) >= 12:
        return 20
    if endpoint in (9, 10, 11):
        return {9: 60, 10: 48, 11: 36}[endpoint]
    if endpoint in (-9, -10, -11):
        return {-9: 8, -10: 14, -11: 18}[endpoint]
    return max(0, min(63, 20 + 6 * endpoint))


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
    assert oracle(0, 4, 0) == 20
    assert oracle(0, 10, 20) == 63
    assert oracle(20, 10, 0) == 0
    assert oracle(0, 0, 12) == 20
    assert oracle(0, 0, 10) == 48
    for phase in range(32):
        assert oracle(phase, phase, phase) == 20


def render_bsasm(unified: list[int]) -> str:
    lut_line = "lut " + " ".join(str(word) for word in unified)
    return f"""# Phase5+ guarded winding LUT16 hardware oracle.
#
# This TX backend consumes predecoded Phase5 symbols. In live Phase5+ mode the
# independent RX BitScrambler supplies those symbols from raw Q4/I4.
#
# Test stream:
#   prime high byte bits 8..12 = first previous endpoint Phase5
#   then each 16-bit pair:
#     low byte  bits 0..4  = middle Phase5
#     high byte bits 8..12 = current Phase5
#
# Shared 1024x16 LUT:
#   L0..L5   = stage-2 guarded P20/G2 DAC
#   L8..L12  = stage-1 5-bit token (30 states used)
#
# State packing:
#   O0..O4   = previous endpoint p while emit executes
#   O26..O30 = stage-2 free bits p[1:0],m[0],c[1:0]
#
# lift reconstructs next p=c in O0..O4 while issuing stage 2, so emit can use
# all O0..O15 for the [D,D] DAC write without losing state.
#
# Steady state is exactly two bundles:
#   emit -> lift -> emit -> lift ...
#
# Stage 1: p[4:2],m[4:1],c[4:2] -> token[4:0]
# Stage 2: p[1:0],m[0],c[1:0],token[4:0] -> guarded DAC
# Middle-sample outliers fall back to the endpoint; large incoherent endpoints
# are damped like Golden. A coherent winding is lifted before the DAC code is
# clipped, exactly once per 50 ns pair.
cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 16
{lut_line}

prime_previous:
    # Prime p from the high byte, then advance to the first [m,c] pair.
    set 0..4 8..12,
    read 16

prime_stage1:
    # Address stage 1 and save all five free bits outside the LUT address.
    set 16..18 O2..O4,
    set 19..22 1..4,
    set 23..25 10..12,
    set 26 O0,
    set 27 O1,
    set 28 0,
    set 29 8,
    set 30 9,
    read 16,
    jmp lift

emit:
    # L now holds stage 2. Emit exact [D,D], issue stage 1 for the next pair,
    # and save that pair's five stage-2 free bits in O26..O30.
    set 0..5 L0..L5,
    set 6..7 L,
    set 8..13 L0..L5,
    set 14..15 L,
    set 16..18 O2..O4,
    set 19..22 1..4,
    set 23..25 10..12,
    set 26 O0,
    set 27 O1,
    set 28 0,
    set 29 8,
    set 30 9,
    read 16,
    write 16

lift:
    # L contains stage-1 token. Stage 2 consumes the five saved free bits.
    # At the same time reconstruct next previous endpoint p=c:
    #   c[1:0] came through O29..O30
    #   c[4:2] is still present in the previous stage-1 address O23..O25.
    set 0 O29,
    set 1 O30,
    set 2..4 O23..O25,
    set 16..20 O26..O30,
    set 21..25 L8..L12,
    jmp emit
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
        "Phase5+ LUT16 self-test passed: "
        "32768/32768 oracle triplets, 10->5->10, "
        f"{TOKEN_COUNT} tokens, {LUT_WORDS}x16 shared LUT, [D,D] write enabled"
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
