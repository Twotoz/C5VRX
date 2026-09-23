#!/usr/bin/env python3
"""Exhaustively prove the live dual-BitScrambler LIFT-FM composition.

This verifies the exact contract between the RX preprocessor and the TX LIFT
backend over the complete live boundary domain:

    previous Phase5 (32) x middle raw Q4/I4 (256) x current raw Q4/I4 (256)

= 2,097,152 combinations.

The RX stage may attach three quality bits to each ring byte; the TX backend
must depend only on the low five exact Phase5 bits.
"""

from __future__ import annotations

from lift_fm_lut16 import (
    build_tables,
    stage1_address,
    stage2_address,
)
from lift_fm_rx_phase import ring_code
from lift_fm_synth import oracle
from train_trajectory_v2 import phase5


def live_backend(
    previous_phase: int,
    middle_raw: int,
    current_raw: int,
    stage1: list[int],
    stage2: list[int],
) -> int:
    middle_code = ring_code(middle_raw)
    current_code = ring_code(current_raw)

    # The upper three bits are supervisory metadata only. The LIFT backend
    # intentionally sees exactly the production Phase5 payload.
    middle = middle_code & 0x1F
    current = current_code & 0x1F

    token = stage1[stage1_address(previous_phase, middle, current)]
    return stage2[stage2_address(previous_phase, middle, current, token)]


def self_test() -> None:
    stage1, stage2, _ = build_tables()

    # First prove that the live ring payload preserves production Phase5 for
    # all possible Q4/I4 bytes, regardless of the quality metadata above it.
    for raw in range(256):
        code = ring_code(raw)
        if (code & 0x1F) != phase5(raw):
            raise AssertionError(
                f"RX Phase5 contract mismatch raw=0x{raw:02x}: "
                f"ring={code & 0x1f} phase5={phase5(raw)}"
            )
        if code > 0xFF:
            raise AssertionError("RX ring code escaped one-byte contract")

    checked = 0
    for previous in range(32):
        for middle_raw in range(256):
            middle = phase5(middle_raw)
            for current_raw in range(256):
                current = phase5(current_raw)
                got = live_backend(
                    previous,
                    middle_raw,
                    current_raw,
                    stage1,
                    stage2,
                )
                want = oracle(previous, middle, current)
                if got != want:
                    raise AssertionError(
                        "dual pipeline mismatch "
                        f"p={previous} middle_raw=0x{middle_raw:02x} "
                        f"current_raw=0x{current_raw:02x}: "
                        f"got={got} want={want}"
                    )
                checked += 1

    expected = 32 * 256 * 256
    if checked != expected:
        raise AssertionError(f"expected {expected} combinations, checked {checked}")

    print(
        "LIFT-FM dual-pipeline self-test passed: "
        f"{checked}/{expected} live boundary combinations exact; "
        "RX quality metadata is invisible to TX LIFT"
    )


if __name__ == "__main__":
    self_test()
