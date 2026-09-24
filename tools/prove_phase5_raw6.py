#!/usr/bin/env python3
"""Exhaustively test whether six routed Q4/I4 bits can replace Phase5(raw)."""

from itertools import combinations

from prove_golden360_capacity import GOLDEN
from train_trajectory_v2 import phase5


def key(raw: int, bits: tuple[int, ...]) -> int:
    return sum(((raw >> bit) & 1) << index for index, bit in enumerate(bits))


results = []
for bits in combinations(range(8), 6):
    buckets: dict[int, list[int]] = {}
    for raw in range(256):
        buckets.setdefault(key(raw, bits), []).append(raw)

    ambiguous_phase_buckets = sum(
        len({phase5(raw) for raw in values}) > 1
        for values in buckets.values()
    )
    conflicting_dac_pairs = 0
    for previous in range(32):
        for values in buckets.values():
            if len({GOLDEN[(previous << 5) | phase5(raw)] & 63
                    for raw in values}) > 1:
                conflicting_dac_pairs += 1
    results.append((conflicting_dac_pairs, ambiguous_phase_buckets, bits))

invariant_xor_masks = [
    mask for mask in range(1, 256)
    if all(phase5(raw) == phase5(raw ^ mask) for raw in range(256))
]

print("6-bit raw projections tested:", len(results))
print("best (conflicting previous-phase/key cells, ambiguous key cells, raw bits):")
for item in sorted(results)[:5]:
    print(item)
print("nonzero phase-preserving XOR directions:", invariant_xor_masks)
assert min(item[0] for item in results) > 0
assert not invariant_xor_masks
