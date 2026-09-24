#!/usr/bin/env python3
"""Test whether choosing the middle I/Q sign axis can resolve Phase5 winding.

Two domains are deliberately compared: a bounded smooth-FM model and all
32^3 phase triplets. The first gives an optimistic capability result; the
second exposes false winding corrections on noisy/discontinuous IQ.
"""

from collections import defaultdict


def wrap32(value: int) -> int:
    return ((value + 16) & 31) - 16


def winding(previous: int, middle: int, current: int) -> int:
    return (wrap32(middle - previous) + wrap32(current - middle)
            - wrap32(current - previous)) // 32


def half_plane(middle: int, axis: int) -> int:
    return ((middle + axis) & 31) >> 4


def smooth_cases():
    cases = defaultdict(list)
    for previous in range(32):
        for first in range(-12, 13):
            for second in range(-12, 13):
                if abs(first - second) > 6:
                    continue
                middle = (previous + first) & 31
                current = (middle + second) & 31
                cases[previous, current].append(
                    (middle, winding(previous, middle, current)))
    return cases


if __name__ == "__main__":
    cases = smooth_cases()
    smooth_winding = sum(k != 0 for values in cases.values() for _, k in values)
    resolvable_endpoint_cells = 0
    optimistic_hits = 0
    universal_flags = {}

    for (previous, current), values in cases.items():
        axis_options = []
        for axis in (0, 8):
            observed = defaultdict(set)
            for middle, k in values:
                observed[half_plane(middle, axis)].add(k)
            if all(len(group) == 1 for group in observed.values()):
                axis_options.append(axis)
        if any(k for _, k in values):
            assert axis_options, (previous, current)
            resolvable_endpoint_cells += 1
            axis = axis_options[0]
            optimistic_hits += sum(k != 0 for _, k in values)

        # Stronger rule: a flag is safe for *every* possible middle phase,
        # rather than only for those accepted by the smooth-motion prior.
        choices = []
        for axis in (0, 8):
            all_middle = defaultdict(set)
            for middle in range(32):
                all_middle[half_plane(middle, axis)].add(
                    winding(previous, middle, current))
            flags = {bit: next(iter(group)) for bit, group in all_middle.items()
                     if len(group) == 1 and 0 not in group}
            choices.append((len(flags), axis, flags))
        _, axis, flags = max(choices, key=lambda item: item[0])
        universal_flags[previous, current] = axis, flags

    universal_hits = 0
    for (previous, current), values in cases.items():
        axis, flags = universal_flags[previous, current]
        universal_hits += sum(
            flags.get(half_plane(middle, axis)) == k
            for middle, k in values if k != 0)

    assert resolvable_endpoint_cells == 544
    assert optimistic_hits == smooth_winding == 2400
    assert universal_hits == 24
    print(f"Adaptive I/Q sign resolves {optimistic_hits}/{smooth_winding} "
          "smooth-model winding cases across 544 endpoint cells")
    print(f"If correction must be safe for every middle phase: "
          f"{universal_hits}/{smooth_winding} winding cases remain")
