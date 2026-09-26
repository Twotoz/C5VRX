#!/usr/bin/env python3
"""Exhaustive safe partial-winding coverage with coarse middle hints.

This is an offline bound, not a schedulable C5 BitScrambler program. A hint
corrects only if ALL middle Phase5 values that share it require the same
nonzero winding for the current endpoint pair. Ambiguous hints keep Golden.
"""

from itertools import combinations

from prove_golden360_capacity import GOLDEN, winding


PHASE = [(GOLDEN[raw] >> 8) & 31 for raw in range(256)]


def evaluate(classes, token_of_raw):
    corrected_raw = total_raw = 0
    corrected_phase = set()
    for previous in range(32):
        for current in range(32):
            safe = []
            for middle_phases in classes:
                outcomes = {winding(previous, middle, current)
                            for middle in middle_phases}
                safe.append(next(iter(outcomes)) if len(outcomes) == 1 else 0)
            for raw, middle in enumerate(PHASE):
                actual = winding(previous, middle, current)
                if actual:
                    total_raw += 1
                predicted = safe[token_of_raw(raw)]
                assert predicted == 0 or predicted == actual
                if predicted:
                    corrected_raw += 1
                    corrected_phase.add((previous, middle, current))
    assert total_raw == 65536
    return corrected_raw, len(corrected_phase)


def raw_bit_classes(bits):
    def token(raw):
        return sum(((raw >> bit) & 1) << i for i, bit in enumerate(bits))

    classes = [set() for _ in range(1 << len(bits))]
    for raw, middle in enumerate(PHASE):
        classes[token(raw)].add(middle)
    return classes, token


def main():
    expected_raw = {
        1: (0, 0, (7,)),
        2: (16384, 2304, (3, 7)),
        3: (25856, 4136, (3, 6, 7)),
        4: (40576, 6696, (2, 3, 6, 7)),
    }
    for width in (1, 2, 3, 4):
        best = max(((*evaluate(*raw_bit_classes(bits)), bits)
                    for bits in combinations(range(8), width)))
        assert best == expected_raw[width]
        print(f"Best {width} direct raw bits {best[2]}:"
              f" {best[0]}/65536 raw winding triples corrected;"
              f" {best[1]}/8192 phase triples covered")
    expected_phase = {2: (20736, 2592), 3: (43264, 5408),
                      4: (57600, 7200)}
    for width in (2, 3, 4):
        classes = [{middle for middle in range(32)
                    if middle >> (5 - width) == token}
                   for token in range(1 << width)]
        raw_corrected, phase_corrected = evaluate(
            classes, lambda raw: PHASE[raw] >> (5 - width))
        assert (raw_corrected, phase_corrected) == expected_phase[width]
        print(f"Top {width} decoded Phase5 bits:"
              f" {raw_corrected}/65536 raw winding triples corrected;"
              f" {phase_corrected}/8192 phase triples covered")
    print("Every reported partial correction is exact; ambiguous cases stay Golden")


if __name__ == "__main__":
    main()
