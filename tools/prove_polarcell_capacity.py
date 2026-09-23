#!/usr/bin/env python3
"""Check the state lower bound for an exact one-bundle Phase5 FM cell.

An output LUT addressed by current raw IQ and a compressed previous state
must distinguish previous phases whenever they admit the same next phase but
require different adjacent deltas.  This remains true for bounded smooth FM.
"""

from itertools import combinations


def wrap32(delta: int) -> int:
    return ((delta + 16) & 31) - 16


def main() -> None:
    max_step = 12  # 135 degrees; includes the requested 110-degree example.
    previous_phases = range(32)
    distinguishable = 0
    for p, q in combinations(previous_phases, 2):
        witnesses = [
            c for c in previous_phases
            if abs(wrap32(c - p)) <= max_step
            and abs(wrap32(c - q)) <= max_step
            and wrap32(c - p) != wrap32(c - q)
        ]
        assert witnesses, (p, q)
        distinguishable += 1

    assert distinguishable == 32 * 31 // 2
    print(f"All {distinguishable} pairs of previous Phase5 states are distinguishable")
    print("Exact adjacent FM requires at least 32 state values (5 bits)")
    print("LUT16 raw8+state2 has 4 addressed states; LUT8 raw8+state3 has 8")

    # Even the 50-ns sum needs the previous endpoint when the two future
    # samples m,c are held fixed.  We can choose m near both previous phases,
    # then choose c=m, so middle cancellation does not remove the distinction.
    for p, q in combinations(previous_phases, 2):
        assert any(
            abs(wrap32(m - p)) <= max_step
            and abs(wrap32(m - q)) <= max_step
            and wrap32(m - p) != wrap32(m - q)
            for m in previous_phases
        )
    print("The same 5-bit lower bound holds for exact adjacent-pair sums")


if __name__ == "__main__":
    main()
