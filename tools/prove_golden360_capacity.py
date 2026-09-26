#!/usr/bin/env python3
"""Exhaustive Phase5 capacity check for a Golden-based 50 ns winding path.

This tests the two-LUT-stage factorization (middle,current)->token, followed by
(previous,token)->DAC, and direct raw-middle-bit hints. It is a lower-bound
probe for this architecture, not a theorem about every possible C5 program.
"""

import math
from itertools import combinations
from pathlib import Path

from train_trajectory_v2 import phase5, scale_rad


GOLDEN = [int(v) for v in next(
    line for line in (Path(__file__).resolve().parents[1] / "main/fm.bsasm")
    .read_text(encoding="utf-8").splitlines() if line.startswith("lut ")
).split()[1:]]
assert len(GOLDEN) == 1024


def golden360_raw(previous_raw, middle_raw, current_raw):
    """Offline reference; not the realtime C5 program."""
    previous, middle, current = map(phase5,
                                    (previous_raw, middle_raw, current_raw))
    golden = GOLDEN[(previous << 5) | current] & 63
    if winding(previous, middle, current) == 0:
        return golden
    return pair_dac(previous, middle, current)


def wrap32(delta):
    return ((delta + 16) & 31) - 16


def winding(previous, middle, current):
    endpoint = wrap32(current - previous)
    pair = wrap32(middle - previous) + wrap32(current - middle)
    return (pair - endpoint) // 32


def interval_winding(previous, middle, current):
    """Endpoint-conditioned interval circuit; the output needs one bit."""
    u = (middle - previous) & 31
    endpoint = wrap32(current - previous)
    if endpoint >= 0:
        return -int(16 <= u <= 16 + endpoint)
    return int(16 + endpoint < u < 16)


def pair_dac(previous, middle, current):
    pair = wrap32(middle - previous) + wrap32(current - middle)
    return scale_rad(pair * 2 * math.pi / 32)


def main():
    assert all(phase5(raw) == ((GOLDEN[raw] >> 8) & 31)
               for raw in range(256))
    representatives = [next(raw for raw in range(256) if phase5(raw) == phase)
                       for phase in range(32)]
    no_winding_cases = 0
    for p in range(32):
        for m in range(32):
            for c in range(32):
                if winding(p, m, c) == 0:
                    assert golden360_raw(representatives[p], representatives[m],
                                         representatives[c]) == (GOLDEN[(p << 5) | c] & 63)
                    no_winding_cases += 1
    print(f"Golden byte-exact no-winding triplets: {no_winding_cases}")
    # A token must distinguish two (middle,current) pairs whenever some
    # previous phase makes them require different final DAC codes.
    rows = {
        tuple(pair_dac(p, m, c) for p in range(32))
        for m in range(32) for c in range(32)
    }
    assert len(rows) == 1024
    print(f"Distinct (middle,current) DAC continuation rows: {len(rows)}")
    print("Required exact token: 10 bits; previous Phase5: 5 bits")
    print("Stage-2 exact address: 15 bits > LUT16's 10 bits")

    # The opposite time order matters for the measured alternating-middle
    # probe: after decoding previous and middle, the next raw byte is current.
    # A direct LUT continuation must retain every (previous,middle) pair;
    # compressing the pair before the final raw-current lookup loses an output.
    pm_rows = {
        tuple((GOLDEN[(p << 5) | c] & 63)
              if winding(p, m, c) == 0 else pair_dac(p, m, c)
              for c in range(32))
        for p in range(32) for m in range(32)
    }
    assert len(pm_rows) == 1024
    print("Distinct (previous,middle) continuation rows: 1024")
    print("Direct final raw-current address: 10 state + 8 raw = 18 bits"
          " > LUT8's 11-bit address")

    # For every direct split of the 15 phase bits into first-stage X10 and
    # second-stage Y5, two X values may share a five-bit token only if their
    # 32 DAC outputs over all Y agree. Stop each split once 33 distinct rows
    # have been witnessed: that already exceeds token5 capacity.
    dac_table = [
        ((GOLDEN[((index & 31) << 5) | ((index >> 10) & 31)] & 63)
         if winding(index & 31, (index >> 5) & 31,
                    (index >> 10) & 31) == 0
         else pair_dac(index & 31, (index >> 5) & 31,
                       (index >> 10) & 31))
        for index in range(1 << 15)
    ]
    for address_bits, token_bits in ((11, 7), (10, 5), (9, 3)):
        y_width = 15 - address_bits
        over_capacity = 0
        feasible_splits = []
        for y_positions in combinations(range(15), y_width):
            x_positions = tuple(position for position in range(15)
                                if position not in y_positions)
            y_masks = [sum(((y >> bit) & 1) << position
                           for bit, position in enumerate(y_positions))
                       for y in range(1 << y_width)]
            rows_for_split = set()
            for x in range(1 << address_bits):
                x_mask = sum(((x >> bit) & 1) << position
                             for bit, position in enumerate(x_positions))
                rows_for_split.add(tuple(dac_table[x_mask | y_mask]
                                         for y_mask in y_masks))
                if len(rows_for_split) > (1 << token_bits):
                    over_capacity += 1
                    break
            else:
                feasible_splits.append((y_positions, len(rows_for_split)))
        total = math.comb(15, y_width)
        print(f"Direct LUT-address{address_bits}/token{token_bits} splits over "
              f"capacity: {over_capacity}/{total}; feasible={feasible_splits[:6]}")
        assert over_capacity == total

    middle_rows = {
        tuple(winding(p, m, c) for p in range(32) for c in range(32))
        for m in range(32)
    }
    assert len(middle_rows) == 32
    print("Distinct middle-phase winding rows: 32 (5-bit middle summary needed)")

    # Endpoint-conditioned compression is real, but becomes available only
    # after current has been decoded.  It cannot replace an earlier,
    # endpoint-independent middle token passed to the next raw lookup.
    conditioned = {1: 0, 2: 0}
    for previous in range(32):
        local_rows = {
            tuple(winding(previous, middle, current)
                  for current in range(32))
            for middle in range(32)
        }
        assert len(local_rows) == 32
        for current in range(32):
            choices = {winding(previous, middle, current)
                       for middle in range(32)}
            assert len(choices) in conditioned
            conditioned[len(choices)] += 1
    assert conditioned == {1: 32, 2: 992}
    print("With BOTH endpoints known, winding choices:"
          " 32 cells need 0 bits, 992 need 1 bit")
    print("With only previous known, every previous needs 32 distinct"
          " middle continuations (5 bits)")
    assert all(interval_winding(p, m, c) == winding(p, m, c)
               for p in range(32) for m in range(32) for c in range(32))
    print("Endpoint-conditioned interval circuit: 32768/32768 exact")

    # All raw Q4 bytes in each direct-bit class are legal middle samples.
    # A correction is unconditionally safe only if every possible phase in
    # that class produces the same nonzero winding for fixed endpoints.
    for bit in range(8):
        classes = [
            {phase5(raw) for raw in range(256) if ((raw >> bit) & 1) == value}
            for value in (0, 1)
        ]
        safe = 0
        for previous in range(32):
            for current in range(32):
                for middle_phases in classes:
                    answers = {winding(previous, middle, current)
                               for middle in middle_phases}
                    safe += len(answers) == 1 and 0 not in answers
        assert safe == 0
        print(f"middle raw bit {bit}: safe nonzero winding cells={safe}")

    # Concrete collision for the intuitive +225-degree 50 ns trajectory:
    # both middle phases have negative I, but one continuation is -135 deg.
    assert winding(0, 10, 20) == 1
    assert winding(0, 20, 20) == 0
    raw10 = next(raw for raw in range(256)
                 if phase5(raw) == 10 and (raw >> 7) == 1)
    raw20 = next(raw for raw in range(256)
                 if phase5(raw) == 20 and (raw >> 7) == 1)
    assert (raw10 >> 7) == (raw20 >> 7)
    raw0 = next(raw for raw in range(256) if phase5(raw) == 0)
    rawc = next(raw for raw in range(256) if phase5(raw) == 20)
    assert golden360_raw(raw0, raw10, rawc) == pair_dac(0, 10, 20)
    assert golden360_raw(raw0, raw20, rawc) == (GOLDEN[20] & 63)
    print("Witness p=0,c=20: m=10 needs +360 correction; m=20 needs Golden")


if __name__ == "__main__":
    main()
