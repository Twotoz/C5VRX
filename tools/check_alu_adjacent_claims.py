#!/usr/bin/env python3
"""Check winding-sign shortcut and the proposed LUT-free Golden base curve."""

from prove_golden360_capacity import GOLDEN, winding, wrap32


def clip6(value: int) -> int:
    return min(63, max(0, value))


wrong_signs = []
for previous in range(32):
    for middle in range(32):
        for current in range(32):
            d0 = wrap32(middle - previous)
            d1 = wrap32(current - middle)
            endpoint = wrap32(current - previous)
            shortcut = (
                1 if d0 >= 0 and d1 >= 0 and endpoint < 0
                else -1 if d0 < 0 and d1 < 0 and endpoint >= 0
                else 0
            )
            if shortcut != winding(previous, middle, current):
                wrong_signs.append((previous, middle, current))
assert not wrong_signs

wrong_base = []
for previous in range(32):
    for current in range(32):
        endpoint = wrap32(current - previous)
        golden = GOLDEN[(previous << 5) | current] & 63
        arithmetic = clip6(20 + 6 * endpoint)
        if golden != arithmetic:
            wrong_base.append((previous, current, golden, arithmetic))

assert len(wrong_base) == 654
assert (8, 18, 48, 63) in wrong_base
print("Winding sign shortcut: exact on all 32768 Phase5 triplets")
print(f"Simple 20+6e curve: {len(wrong_base)}/1024 Golden cells differ")
print("Counterexample: p=8,c=18,e=+10 -> Golden 48, simple curve 63")
