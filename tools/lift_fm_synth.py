#!/usr/bin/env python3
"""LIFT-FM exact Phase5 adjacent-FM decomposition.

This is a proof/generator for the exact phase-lifting core proposed for issue #23.

It does NOT claim that raw Q4/I4 -> Phase5 is already solved in two live
BitScrambler bundles. Instead it proves the smaller problem exactly:

    p,m,c are exact 5-bit Phase5 states
    d0 = wrap32(m-p)
    d1 = wrap32(c-m)
    pair = d0+d1              # deliberately not wrapped again
    DAC  = clamp(20 + 6*pair)

The 15 input state bits decompose into two LUTs:
  stage 1 address (11 bits): p[4:2], m[4:0], c[4:2]
  stage 1 output:             6-bit equivalence-class token (34 used)
  stage 2 address (10 bits):  token[5:0], p[1:0], c[1:0]
  stage 2 output:             exact 6-bit DAC code

All 32^3 = 32768 Phase5 triplets are exhaustively verified.

The remaining live problem is front-end scheduling: production receives two
raw 8-bit Q4/I4 symbols per 50 ns, and exact Q4->Phase5 decode itself consumes
LUT information. This script intentionally keeps that boundary explicit.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "main" / "lift_fm_phase_lut.h"

PHASE_STATES = 32
STAGE1_SIZE = 2048
STAGE2_SIZE = 1024
TOKEN_BITS = 6
USED_TOKEN_COUNT = 34

# Bit positions in the 15-bit concatenated state word
# [p0..p4, m0..m4, c0..c4].
BOUND_BITS = (2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14)
FREE_BITS = (0, 1, 10, 11)


def wrap_phase5(delta: int) -> int:
    if delta > 15:
        delta -= 32
    elif delta < -16:
        delta += 32
    return delta


def pair_sum(previous: int, middle: int, current: int) -> int:
    return wrap_phase5(middle - previous) + wrap_phase5(current - middle)


def endpoint(previous: int, current: int) -> int:
    return wrap_phase5(current - previous)


def lift(previous: int, middle: int, current: int) -> int:
    """Return the exact winding lift in {-1,0,+1}."""
    delta = pair_sum(previous, middle, current) - endpoint(previous, current)
    assert delta % 32 == 0
    out = delta // 32
    assert out in (-1, 0, 1)
    return out


def _signed5(value: int) -> int:
    value &= 31
    return value - 32 if value >= 16 else value


def packed_simd_deltas(previous: int, middle: int, current: int) -> tuple[int, int]:
    """Model the proposed x8 packed two-lane BitScrambler add.

    Each Phase5 state is embedded into Z256 as 8*phase. The low byte computes
    middle-previous and the high byte computes current-middle in one 16-bit
    addition. A carry from the low byte can only touch one of the three guard
    bits in the high byte, so high bits [7:3] remain exact.
    """
    ep = (previous * 8) & 0xFF
    em = (middle * 8) & 0xFF
    ec = (current * 8) & 0xFF

    initial = (((-em) & 0xFF) << 8) | ((-ep) & 0xFF)
    addend = (ec << 8) | em
    result = (initial + addend) & 0xFFFF

    d0 = _signed5((result & 0xFF) >> 3)
    d1 = _signed5(((result >> 8) & 0xFF) >> 3)
    return d0, d1


def map_pair_to_dac(total: int) -> int:
    """Production P20/G2 mapping for integer Phase5 steps.

    One Phase5 step is exactly eight Phase8 units. P20/G2 maps that to
    6 DAC codes per Phase5 step before the legal 0..63 clamp.
    """
    return max(0, min(63, 20 + 6 * total))


def oracle(previous: int, middle: int, current: int) -> int:
    return map_pair_to_dac(pair_sum(previous, middle, current))


def stage1_address(previous: int, middle: int, current: int) -> int:
    return (
        ((previous >> 2) & 0x07)
        | ((middle & 0x1F) << 3)
        | (((current >> 2) & 0x07) << 8)
    )


def stage2_address(previous: int, current: int, token: int) -> int:
    return (
        (previous & 0x03)
        | ((current & 0x03) << 2)
        | ((token & 0x3F) << 4)
    )


def _future_vector(p_hi: int, middle: int, c_hi: int) -> tuple[int, ...]:
    values = []
    for p_lo in range(4):
        for c_lo in range(4):
            previous = (p_hi << 2) | p_lo
            current = (c_hi << 2) | c_lo
            values.append(oracle(previous, middle, current))
    return tuple(values)


def regenerate() -> tuple[list[int], list[int]]:
    vectors: dict[tuple[int, ...], int] = {}
    stage1 = [0] * STAGE1_SIZE

    for p_hi in range(8):
        for middle in range(32):
            for c_hi in range(8):
                vector = _future_vector(p_hi, middle, c_hi)
                token = vectors.setdefault(vector, len(vectors))
                address = p_hi | (middle << 3) | (c_hi << 8)
                stage1[address] = token

    if len(vectors) != USED_TOKEN_COUNT:
        raise AssertionError(
            f"decomposition drift: expected {USED_TOKEN_COUNT} tokens, got {len(vectors)}"
        )
    if max(stage1) >= (1 << TOKEN_BITS):
        raise AssertionError("stage-1 token no longer fits in six bits")

    stage2 = [20] * STAGE2_SIZE
    by_token = [None] * len(vectors)
    for vector, token in vectors.items():
        by_token[token] = vector

    for token, vector in enumerate(by_token):
        assert vector is not None
        for p_lo in range(4):
            for c_lo in range(4):
                free_index = (p_lo << 2) | c_lo
                code = vector[free_index]
                address = p_lo | (c_lo << 2) | (token << 4)
                stage2[address] = code

    return stage1, stage2


def exhaustive_verify(stage1: list[int], stage2: list[int]) -> None:
    checked = 0
    lifts = {-1: 0, 0: 0, 1: 0}

    for previous in range(32):
        for middle in range(32):
            for current in range(32):
                total = pair_sum(previous, middle, current)
                ep = endpoint(previous, current)
                k = lift(previous, middle, current)
                assert total == ep + 32 * k
                lifts[k] += 1

                packed_d0, packed_d1 = packed_simd_deltas(
                    previous, middle, current
                )
                assert packed_d0 == wrap_phase5(middle - previous)
                assert packed_d1 == wrap_phase5(current - middle)
                assert packed_d0 + packed_d1 == total

                token = stage1[stage1_address(previous, middle, current)]
                got = stage2[stage2_address(previous, current, token)]
                want = oracle(previous, middle, current)
                if got != want:
                    raise AssertionError(
                        "LIFT-FM mismatch "
                        f"p={previous} m={middle} c={current}: "
                        f"token={token} got={got} want={want}"
                    )
                checked += 1

    assert checked == 32768
    assert sum(lifts.values()) == checked


def _rows(values: Iterable[int], width: int = 16) -> str:
    values = list(values)
    return "\n".join(
        "    " + ", ".join(str(v) for v in values[i:i + width]) + ","
        for i in range(0, len(values), width)
    )


def render_header(stage1: list[int], stage2: list[int]) -> str:
    digest = hashlib.sha256(bytes(stage1) + bytes(stage2)).hexdigest()
    return f"""/* Generated by tools/lift_fm_synth.py -- do not hand-edit.
 *
 * Exact Phase5 adjacent-FM lifting decomposition:
 *   stage1: p[4:2] + m[4:0] + c[4:2] -> 6-bit token ({USED_TOKEN_COUNT} used)
 *   stage2: token + p[1:0] + c[1:0] -> exact 6-bit P20/G2 DAC code
 *
 * This table is an exact phase-domain proof. It does not by itself solve the
 * live raw-Q4 -> Phase5 scheduling problem.
 *
 * sha256(stage1||stage2) = {digest}
 */
#pragma once
#include <stdint.h>

#define C5VRX_LIFT_FM_TOKEN_BITS {TOKEN_BITS}u
#define C5VRX_LIFT_FM_TOKEN_COUNT {USED_TOKEN_COUNT}u
#define C5VRX_LIFT_FM_STAGE1_SIZE {STAGE1_SIZE}u
#define C5VRX_LIFT_FM_STAGE2_SIZE {STAGE2_SIZE}u

static const uint8_t c5vrx_lift_fm_stage1[{STAGE1_SIZE}] = {{
{_rows(stage1)}
}};

static const uint8_t c5vrx_lift_fm_stage2[{STAGE2_SIZE}] = {{
{_rows(stage2)}
}};
"""


def self_test(check_generated: bool = True) -> None:
    stage1, stage2 = regenerate()
    exhaustive_verify(stage1, stage2)
    rendered = render_header(stage1, stage2)

    if check_generated:
        if not HEADER.exists():
            raise AssertionError(f"missing generated header: {HEADER}")
        if HEADER.read_text(encoding="utf-8") != rendered:
            raise AssertionError(
                "generated LIFT-FM table drift; run "
                "python3 tools/lift_fm_synth.py --write --self-test"
            )

    digest = hashlib.sha256(bytes(stage1) + bytes(stage2)).hexdigest()
    print(
        "LIFT-FM self-test passed: "
        "32768/32768 exact Phase5 triplets, "
        f"{len(set(stage1))} stage-1 tokens, "
        f"sha256={digest[:16]}"
    )


def write_generated() -> None:
    stage1, stage2 = regenerate()
    exhaustive_verify(stage1, stage2)
    HEADER.write_text(render_header(stage1, stage2), encoding="utf-8")
    print(f"wrote {HEADER.relative_to(ROOT)}")


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
