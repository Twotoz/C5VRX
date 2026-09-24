#!/usr/bin/env python3
"""Check every PolarState8 table entry and the actual bundle dataflow."""

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "legacy" / "c5vrx2" / "tools"))
from bs_model import parse, simulate  # noqa: E402
from build_polarstate8 import ASM, generate, state3, word  # noqa: E402


def reference(raw):
    state = 0
    output = []
    for sample in raw:
        result = word(state, sample)
        output.append(result & 63)
        state = ((sample >> 7) << 2) | (result >> 6)
    return output[:-1]


def main():
    assert ASM.read_text(encoding="utf-8") == generate()
    source = ASM.read_text(encoding="utf-8")
    cfg, lut, bundles, _ = parse(source)
    assert cfg["lut_width_bits"] == "8"
    assert len(lut) == 2048 and len(bundles) == 2
    for state in range(8):
        for raw in range(256):
            assert lut[(state << 8) | raw] == word(state, raw)
            if state == state3(raw):
                assert (word(state, raw) & 63) == 20
    rng = random.Random(0xC5A8)
    cases = [[raw] * 80 for raw in range(256)]
    cases += [[rng.randrange(256) for _ in range(1024)] for _ in range(8)]
    # The model continues across the actual 32 KiB cyclic DMA ring boundary;
    # resetting state per DMA block would fail this reference comparison.
    cases.append([rng.randrange(256) for _ in range(32768 + 67)])
    for raw in cases:
        actual = [x & 63 for x in simulate(source, raw, len(raw) - 1)]
        assert actual == reference(raw)
    print("PolarState8: 2048/2048 words, 265 streams, 32 KiB wrap exact")


if __name__ == "__main__":
    main()
