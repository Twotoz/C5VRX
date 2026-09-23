#!/usr/bin/env python3
"""Exhaustive Phase5-bin winding observability for coarse middle observations.

This is a phase-domain oracle, not a model of raw IQ noise or of the BS ISA.
The smooth-motion gate is an explicit assumption, never a live-data guarantee.
"""

from collections import defaultdict


def wrap32(value: int) -> int:
    return ((value + 16) & 31) - 16


def evaluate(quadrant_bits: int, max_step: int = 12, max_accel: int = 6):
    shift = 5 - quadrant_bits
    observed = defaultdict(set)
    cases = []
    for previous in range(32):
        for first in range(-max_step, max_step + 1):
            for second in range(-max_step, max_step + 1):
                if abs(first - second) > max_accel:
                    continue
                middle = (previous + first) & 31
                current = (middle + second) & 31
                endpoint = wrap32(current - previous)
                winding = (first + second - endpoint) // 32
                key = previous, current, middle >> shift
                observed[key].add(winding)
                cases.append((key, winding))

    safe = {key: next(iter(values)) for key, values in observed.items()
            if len(values) == 1 and 0 not in values}
    winding_cases = sum(winding != 0 for _, winding in cases)
    corrected = sum(safe.get(key) == winding for key, winding in cases if winding)
    wrong_on_model = sum(key in safe and safe[key] != winding for key, winding in cases)

    # Apply the same smooth-model table to arbitrary phase triplets. These
    # mismatches are the reason a sign/quadrant hint cannot promise clean video
    # under noisy or discontinuous IQ.
    wrong_on_arbitrary = 0
    for previous in range(32):
        for current in range(32):
            for middle in range(32):
                first = wrap32(middle - previous)
                second = wrap32(current - middle)
                winding = (first + second - wrap32(current - previous)) // 32
                key = previous, current, middle >> shift
                wrong_on_arbitrary += key in safe and safe[key] != winding

    return winding_cases, corrected, wrong_on_model, wrong_on_arbitrary


if __name__ == "__main__":
    for bits in (1, 2):
        total, corrected, wrong_model, wrong_arbitrary = evaluate(bits)
        print(f"middle bits={bits}: corrected={corrected}/{total} "
              f"({corrected / total:.1%}), wrong in smooth model={wrong_model}, "
              f"wrong on arbitrary 32^3 triplets={wrong_arbitrary}")
