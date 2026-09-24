#!/usr/bin/env python3
"""Deterministic smoke score for the PolarState8 geometric seed.

This is a synthetic Q4/I4 trajectory, not a prediction of live RF quality.
"""

import math
import random

from build_polarstate8 import cvbs, word


def main():
    rng = random.Random(7)
    phase = 0.0
    state = 0
    squared = rails = pedestal = 0
    count = 30000
    for tick in range(count):
        step = 0.32 * math.sin(tick * 0.00031) + 0.09 * math.sin(tick * 0.009)
        phase += step
        i = max(-8, min(7, round(6 * math.cos(phase) + rng.gauss(0, 0.6))))
        q = max(-8, min(7, round(6 * math.sin(phase) + rng.gauss(0, 0.6))))
        raw = ((i & 15) << 4) | (q & 15)
        result = word(state, raw)
        state = ((raw >> 7) << 2) | (result >> 6)
        dac = result & 63
        ideal = cvbs(2 * step)
        squared += (dac - ideal) ** 2
        rails += dac in (0, 63)
        pedestal += dac == 20
    print(f"Synthetic adjacent DAC RMS error: {(squared / count) ** 0.5:.2f} codes")
    print(f"Rail samples: {100 * rails / count:.2f}%")
    print(f"Pedestal samples: {100 * pedestal / count:.2f}%")


if __name__ == "__main__":
    main()
