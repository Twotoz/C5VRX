#!/usr/bin/env python3
"""Capacity checks for the proposed (previous,middle)->token4, token+current.

The teacher uses Phase5's 32 phase bins and the repository's production
P20/G2 6-bit DAC calibration. This checks exactness of the stated architecture,
not the visual quality of a learned approximation on a particular RF trace.
"""

import math

from train_trajectory_v2 import scale_rad


def wrap32(value: int) -> int:
    return ((value + 16) & 31) - 16


def teacher(previous: int, middle: int, current: int, adjacent: bool):
    d0 = wrap32(middle - previous)
    d1 = wrap32(current - middle)
    degrees_per_bin = 2.0 * math.pi / 32.0
    if adjacent:
        return (scale_rad(2 * d0 * degrees_per_bin),
                scale_rad(2 * d1 * degrees_per_bin))
    pair = scale_rad((d0 + d1) * degrees_per_bin)
    return pair, pair


def continuation_count(adjacent: bool) -> int:
    # Identical tokens require identical output for every possible current.
    return len({
        tuple(teacher(previous, middle, current, adjacent)
              for current in range(32))
        for previous in range(32) for middle in range(32)
    })


if __name__ == "__main__":
    adjacent = continuation_count(True)
    pair_sum = continuation_count(False)
    assert adjacent == 224 and pair_sum == 1024
    print(f"Exact adjacent DAC pair needs {adjacent} distinct tokens "
          f"({(adjacent - 1).bit_length()} bits)")
    print(f"Exact unwrapped 50 ns pair DAC needs {pair_sum} distinct tokens "
          f"({(pair_sum - 1).bit_length()} bits)")
    print("A four-bit token can only approximate either teacher")
