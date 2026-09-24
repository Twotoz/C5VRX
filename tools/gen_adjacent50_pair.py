#!/usr/bin/env python3
"""Generate the two-bundle Adjacent50 backend and its raw-pair CPU table.

This is a host-verified experimental backend. The 128 KiB table is copied to
internal RAM and applied to the DMA ring ahead of TX. Live throughput and ring
ownership are not yet proven; the first hardware run showed static and a crash.
"""

from pathlib import Path
import random
import statistics
import struct
import sys

from prove_golden360_capacity import GOLDEN, winding, wrap32
from train_trajectory_v2 import phase5

ROOT = Path(__file__).resolve().parents[1]
ASM = ROOT / "tools/fm_adjacent50_pair.bsasm"
PRODUCTION_ASM = ROOT / "main/fm_adjacent50_pair.bsasm"
PAIR_TABLE = ROOT / "tools/cpu_phase_bench/main/pair_lut.bin"
PRODUCTION_PAIR_TABLE = ROOT / "main/adjacent50_pair_lut.bin"
PHASE_TABLE = ROOT / "tools/cpu_phase_bench/main/phase5_lut.bin"


def signed5(value):
    return value - 32 if value & 16 else value


def golden_profile():
    return {
        delta: round(statistics.median(
            GOLDEN[(p << 5) | ((p + delta) & 31)] & 63
            for p in range(32)
        ))
        for delta in range(-16, 16)
    }


PROFILE = golden_profile()


def dac_from_pair(d0, d1):
    total = signed5(d0) + signed5(d1)
    if total < -16:
        return 0
    if total > 15:
        return 63
    return PROFILE[total]


def pack_raw_pair(m_raw, c_raw):
    middle = phase5(m_raw)
    current = phase5(c_raw)
    d1 = (current - middle) & 31
    return (middle | ((d1 & 7) << 5) | (current << 8)
            | ((d1 >> 3) << 13))


def main():
    words = []
    for high in range(32):
        for low in range(32):
            d0 = (low - high) & 31
            words.append(dac_from_pair(high, low) | (d0 << 8))
    asm = (
        "# Adjacent50 phase-pair backend; requires the raw-pair CPU bridge.\n"
        "# Input pair low: m[4:0], d1[2:0]; high: c[4:0], d1[4:3].\n"
        "# Shared LUT16: stage1 p5+m5 -> d0_5 in L8..12;\n"
        "# stage2 d0_5+d1_5 -> DAC6 in L0..5.\n"
        "cfg prefetch true\n"
        "cfg eof_on downstream\n"
        "cfg trailing_bytes 0\n"
        "cfg lut_width_bits 16\n"
        + "lut " + " ".join(map(str, words)) + "\n\n"
        "prime_previous:\n"
        "    set 26..30 8..12,\n"
        "    read 16\n\n"
        "delta0_and_emit:\n"
        "    set 0..5 L0..L5,\n"
        "    set 6..7 L,\n"
        "    set 8..13 L0..L5,\n"
        "    set 14..15 L,\n"
        "    set 16..20 0..4,\n"
        "    set 21..25 O26..O30,\n"
        "    set 26..30 O26..O30,\n"
        "    write 16\n\n"
        "pair_dac_and_next:\n"
        "    set 16..18 5..7,\n"
        "    set 19..20 13..14,\n"
        "    set 21..25 L8..L12,\n"
        "    set 26..30 8..12,\n"
        "    read 16,\n"
        "    jmp delta0_and_emit\n"
    )
    ASM.write_text(asm, encoding="utf-8")
    PRODUCTION_ASM.write_text(asm, encoding="utf-8")

    PAIR_TABLE.parent.mkdir(parents=True, exist_ok=True)
    PAIR_TABLE.write_bytes(b"".join(
        struct.pack("<H", pack_raw_pair(i & 255, i >> 8))
        for i in range(65536)
    ))
    PRODUCTION_PAIR_TABLE.write_bytes(PAIR_TABLE.read_bytes())
    PHASE_TABLE.write_bytes(bytes(phase5(i) for i in range(256)))
    print(f"generated LUT16 backend and {PAIR_TABLE.stat().st_size}-byte "
          "exact raw-pair table")

    # One LUT address serves two independent meanings in separate word fields.
    for p in range(32):
        for m in range(32):
            assert ((words[(p << 5) | m] >> 8) & 31) == ((m - p) & 31)
    for d0 in range(32):
        for d1 in range(32):
            assert (words[(d0 << 5) | d1] & 63) == dac_from_pair(d0, d1)
    for raw in range(65536):
        pair = pack_raw_pair(raw & 255, raw >> 8)
        m = pair & 31
        c = (pair >> 8) & 31
        d1 = ((pair >> 5) & 7) | (((pair >> 13) & 3) << 3)
        assert m == phase5(raw & 255)
        assert c == phase5(raw >> 8)
        assert d1 == ((c - m) & 31)

    no_winding = 0
    exact = 0
    abs_error = 0
    maximum = 0
    for p in range(32):
        for m in range(32):
            for c in range(32):
                d0 = wrap32(m - p) & 31
                d1 = wrap32(c - m) & 31
                actual = dac_from_pair(d0, d1)
                if winding(p, m, c):
                    assert actual == (63 if winding(p, m, c) > 0 else 0)
                else:
                    golden = GOLDEN[(p << 5) | c] & 63
                    error = abs(actual - golden)
                    no_winding += 1
                    exact += error == 0
                    abs_error += error
                    maximum = max(maximum, error)
    print(f"32768 triplets: exact winding rails; no-winding "
          f"{exact}/{no_winding} byte-exact Golden, "
          f"MAE={abs_error/no_winding:.4f} DAC code, max={maximum}")

    sys.path.insert(0, str(ROOT / "legacy/c5vrx2/tools"))
    from bs_model import simulate
    rng = random.Random(0xA50)
    raw = [rng.randrange(256) for _ in range(1204)]
    stream = []
    for i in range(0, len(raw), 2):
        word = pack_raw_pair(raw[i], raw[i + 1])
        stream.extend((word & 255, word >> 8))
    expected = []
    for i in range(1, len(raw) - 2, 2):
        p, m, c = map(phase5, raw[i:i + 3])
        expected.append(dac_from_pair((m - p) & 31, (c - m) & 31))
    observed = simulate(asm, stream, len(expected) * 2 + 2)[::2]
    assert observed[1:] == expected, (
        next((i for i, (a, b) in enumerate(zip(observed[1:], expected))
              if a != b), None), observed[:8], expected[:8]
    )
    print(f"BitScrambler model: {len(expected)} sequential 50-ns pairs exact "
          "after prime")


if __name__ == "__main__":
    main()
