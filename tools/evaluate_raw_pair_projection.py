#!/usr/bin/env python3
"""Exhaustive lower bound for an 11-bit raw IQ pair discriminator LUT.

For each fixed selection of 11 of 16 input bits, an ideal LUT is allowed to
choose the most frequent exact Phase5 adjacent delta at each address.  This is
an optimistic ceiling: a real BitScrambler pipeline has additional state and
timing constraints.  No RF distribution is assumed; all raw pairs are equal.
"""

from itertools import combinations

import numpy as np

from range_demod_bench import PHASE5


def main() -> None:
    raw = np.arange(256, dtype=np.uint16)
    phase = np.asarray(PHASE5, dtype=np.int16)
    delta = (((phase[None, :] - phase[:, None] + 16) & 31) - 16).astype(np.int16)
    delta = (delta.ravel() + 16).astype(np.int32)

    essential = [
        any(PHASE5[x] != PHASE5[x ^ (1 << bit)] for x in range(256))
        for bit in range(8)
    ]
    print("essential raw bits for exact Phase5:", essential, flush=True)

    projections = {}
    for width in range(9):
        for bits in combinations(range(8), width):
            code = np.zeros(256, dtype=np.uint16)
            for dst, src in enumerate(bits):
                code |= ((raw >> src) & 1) << dst
            projections[bits] = code

    best = (-1, None)
    exact_choices = 0
    tested = 0
    for p_width in range(3, 9):
        c_width = 11 - p_width
        if c_width > 8 or c_width < 3:
            continue
        for p_bits in combinations(range(8), p_width):
            p_code = projections[p_bits][:, None]
            for c_bits in combinations(range(8), c_width):
                c_code = projections[c_bits][None, :]
                address = (p_code | (c_code << p_width)).ravel().astype(np.int32)
                hist = np.bincount(address * 32 + delta, minlength=2048 * 32)
                exact = int(hist.reshape(2048, 32).max(axis=1).sum())
                tested += 1
                if exact == 65536:
                    exact_choices += 1
                if exact > best[0]:
                    best = (exact, (p_bits, c_bits))
    print("projections tested:", tested)
    print("exact projections:", exact_choices)
    print("best exact delta matches:", best[0], "/ 65536", f"({best[0] / 65536:.2%})")
    print("best previous/current bit positions:", best[1])

    p_bits, c_bits = best[1]
    p_code = projections[p_bits][:, None]
    c_code = projections[c_bits][None, :]
    address = (p_code | (c_code << len(p_bits))).ravel().astype(np.int32)
    hist = np.bincount(address * 32 + delta, minlength=2048 * 32)
    representative = hist.reshape(2048, 32).argmax(axis=1)
    error = np.abs(representative[address].astype(np.int16) - delta)
    error = np.minimum(error, 32 - error)
    print("best-projection angular error: mean/p95/max bins:",
          round(float(error.mean()), 2), int(np.percentile(error, 95)), int(error.max()))
    print("best-projection angular error: mean/p95 degrees:",
          round(float(error.mean()) * 11.25, 1),
          round(float(np.percentile(error, 95)) * 11.25, 1))


if __name__ == "__main__":
    main()
