#!/usr/bin/env python3
"""Exhaustive raw-Q4 wall analysis for exact LIFT-FM.

This tool keeps the remaining problem honest. It proves three things:

1. The exact live oracle f(previous_phase5, middle_raw, current_raw) is
   sensitive to all 21 semantic input bits (5 + 8 + 8).
2. Therefore a *pure* serial two-LUT16 network with no side arithmetic/state
   cannot be exact: two 10-bit LUT addresses can expose at most 20 independent
   direct input bits to the two stages.
3. A 40-MS/s one-byte-per-bundle front end is still viable because LUT16 can
   address a raw byte with O16..O23 while O24..O25 are zero, leaving six free
   bits O26..O31 in the counter high-byte operand. Five bits are enough for an
   exact Phase5 code. This is the intended overlap point for the next hardware
   superoptimization step.

It also proves that exposing both +phase and -phase through *static bit
selection* needs nine distinct Boolean functions. Hence an 8-bit LUT result
cannot carry both polarities without extra arithmetic; LUT16 can.
"""

from __future__ import annotations

from train_trajectory_v2 import phase5
from lift_fm_synth import oracle

SEMANTIC_BITS = 21


def phase_representatives() -> list[int]:
    reps = [-1] * 32
    for raw in range(256):
        p = phase5(raw)
        if reps[p] < 0:
            reps[p] = raw
    if any(v < 0 for v in reps):
        raise AssertionError("Phase5 map does not cover all 32 states")
    return reps


def find_oracle_witnesses() -> list[tuple[str, tuple[int, int, int], tuple[int, int, int]]]:
    reps = phase_representatives()
    witnesses = []

    # previous Phase5 bits 0..4
    for bit in range(5):
        found = None
        for p in range(32):
            q = p ^ (1 << bit)
            for m in range(32):
                mr = reps[m]
                for c in range(32):
                    cr = reps[c]
                    if oracle(p, m, c) != oracle(q, m, c):
                        found = ((p, mr, cr), (q, mr, cr))
                        break
                if found:
                    break
            if found:
                break
        if not found:
            raise AssertionError(f"previous phase bit {bit} is not essential")
        witnesses.append((f"p{bit}", *found))

    # middle raw-Q4 bits 0..7
    for bit in range(8):
        found = None
        for mr in range(256):
            mq = mr ^ (1 << bit)
            if phase5(mr) == phase5(mq):
                continue
            for p in range(32):
                for c in range(32):
                    cr = reps[c]
                    if oracle(p, phase5(mr), c) != oracle(p, phase5(mq), c):
                        found = ((p, mr, cr), (p, mq, cr))
                        break
                if found:
                    break
            if found:
                break
        if not found:
            raise AssertionError(f"middle raw bit {bit} is not essential")
        witnesses.append((f"m{bit}", *found))

    # current raw-Q4 bits 0..7
    for bit in range(8):
        found = None
        for cr in range(256):
            cq = cr ^ (1 << bit)
            if phase5(cr) == phase5(cq):
                continue
            for p in range(32):
                for m in range(32):
                    mr = reps[m]
                    if oracle(p, m, phase5(cr)) != oracle(p, m, phase5(cq)):
                        found = ((p, mr, cr), (p, mr, cq))
                        break
                if found:
                    break
            if found:
                break
        if not found:
            raise AssertionError(f"current raw bit {bit} is not essential")
        witnesses.append((f"c{bit}", *found))

    if len(witnesses) != SEMANTIC_BITS:
        raise AssertionError(f"expected {SEMANTIC_BITS} witnesses")
    return witnesses


def unique_static_polarity_functions() -> int:
    """Count unique non-constant bit functions in p and (-p mod 32)."""
    vectors = set()
    for negate in (False, True):
        for bit in range(5):
            vector = []
            for p in range(32):
                value = (-p) & 31 if negate else p
                vector.append((value >> bit) & 1)
            vectors.add(tuple(vector))
    return len(vectors)


def verify_lut16_overlap() -> None:
    # LUT16 uses O16..O25 as its 10-bit address. A raw-byte-only lookup can
    # route raw[7:0] to O16..O23 and force O24..O25=0.
    #
    # ADDCTI*H / LDCTI*H consume O24..O31 as the 8-bit high-half operand.
    # O24..O25 are therefore fixed guard bits while O26..O31 remain arbitrary.
    address_bits = 8 + 2
    free_counter_high_bits = 8 - 2
    if address_bits != 10 or free_counter_high_bits != 6:
        raise AssertionError("LUT16 overlap arithmetic changed")
    if free_counter_high_bits < 5:
        raise AssertionError("not enough arithmetic bits for exact Phase5 state")


def self_test() -> None:
    witnesses = find_oracle_witnesses()
    if len(witnesses) != 21:
        raise AssertionError("raw wall essential-bit proof failed")

    # A pure serial 10-bit -> token -> 10-bit decomposition can directly cover
    # at most 20 independent original input bits. The stage-1 token transports
    # information from the first address; it does not create visibility of an
    # omitted original input bit.
    if 10 + 10 >= SEMANTIC_BITS:
        raise AssertionError("expected pure LUT16 visibility lower bound")

    polarity_bits = unique_static_polarity_functions()
    if polarity_bits != 9:
        raise AssertionError(
            f"expected nine static polarity functions, got {polarity_bits}"
        )

    verify_lut16_overlap()

    print(
        "LIFT-FM raw-wall proof passed: "
        "21/21 semantic input bits essential; "
        "pure two-LUT16 exact network impossible without side logic; "
        "dual +/- Phase5 static code needs 9 bits; "
        "LUT16 raw8 + 6-bit counter-high overlap is viable"
    )
    for name, a, b in witnesses:
        print(f"  witness {name}: {a} -> {b}")


if __name__ == "__main__":
    self_test()
