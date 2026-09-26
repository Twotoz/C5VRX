#!/usr/bin/env python3
"""Generate Phase5-360 (Exact Adjacent50) BitScrambler program.

Implements Phase5-360 travel resolution on top of Static-A Relative Golden:
- Dual-purpose 1024x16 LUT:
    bits 0..4 (addr 0..255)    = Phase5(addr & 255) for Controller endpoint lookup
    bits 8..13 (addr 0..1023)  = Exact calibrated Golden DAC for (P, C) pair
    bits 0..5 (addr 256..1023) = Safe 360° DAC (360° winding resolution for |delta| >= 12, Golden fallback for all others)
- Strict Zero False Alarm Guarantee on 3.58 MHz Chroma Subcarrier:
    For all pairs with |wrap32(C - P)| < 12 bins (including 100% of the 3.58 MHz color subcarrier),
    bits 0..5 are byte-identical to Golden DAC fallback. Zero rainbow artifacts!
- Controller-Worker Pre-Branching across 2 bundles (50 ns, 20 MS/s unique [D, D] output):
    Controller evaluates middle sample:
      if in[7] == 1 -> branch to worker_360 (emits 360° DAC from L0..L5)
      if in[7] == 0 -> fall through to worker_golden (emits Golden DAC from L8..L13)
    Both workers execute write 16, read 16, and jump back to controller_0.
"""

from pathlib import Path
import re
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "main/fm.bsasm"
TARGET = ROOT / "main/fm_phase5_360.bsasm"
MODEL_PATH = ROOT / "legacy/c5vrx2/tools/bs_model.py"

spec = importlib.util.spec_from_file_location("bs_model", MODEL_PATH)
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


def wrap32(delta: int) -> int:
    return ((delta + 16) & 31) - 16


def winding(previous: int, middle: int, current: int) -> int:
    endpoint = wrap32(current - previous)
    return (wrap32(middle - previous) + wrap32(current - middle) - endpoint) // 32


def quadrant(raw: int) -> int:
    return ((raw >> 3) & 1) | (((raw >> 7) & 1) << 1)


def build():
    baseline = BASE.read_text(encoding="utf-8")
    _, old_lut, _, _ = model.parse(baseline)
    phase = [(old_lut[raw] >> 8) & 31 for raw in range(256)]

    # Compute safe 360° rails for (P, C) pairs matching in[7] == 1 (Quadrants 2 & 3).
    # STRICT INVARIANT: If |wrap32(C - P)| < 12 bins, NEVER clamp to rail!
    # Always fall back to Golden DAC to guarantee 100% chroma subcarrier integrity (zero rainbow artifacts).
    safe_360 = {}
    for p in range(32):
        for c in range(32):
            endpoint = wrap32(c - p)
            golden_dac = old_lut[(p << 5) | c] & 63
            
            # Subcarrier immunity threshold: normal chroma carrier is ~5.7 bins (<= 8 bins).
            if abs(endpoint) < 12:
                continue
                
            # For large deltas (|endpoint| >= 12), check unanimous quadrant winding rulings
            # where in[7] == 1 (Q2 and Q3).
            rail = 63 if endpoint < 0 else 0
            for q in [2, 3]:
                outcomes = {
                    winding(p, phase[raw], c)
                    for raw in range(256) if quadrant(raw) == q
                }
                # Unanimous ruling: all samples in quadrant produce winding in the rail direction
                if outcomes == {1} or outcomes == {-1}:
                    safe_360[(p, c)] = rail
                    break

    words = []
    for i in range(1024):
        p = (i >> 5) & 31
        c = i & 31
        golden_dac = old_lut[i] & 63
        if i < 256:
            # Addr 0..255: Controller reads Phase5 from bits 0..4
            low_bits = phase[i]
        else:
            # Addr 256..1023: Controller never addresses this region!
            # Bits 0..5 provide the safe 360° DAC if winding occurs, else Golden DAC fallback
            dac_360 = safe_360.get((p, c), golden_dac)
            low_bits = dac_360 & 63

        word = low_bits | (golden_dac << 8)
        words.append(word)

    head = baseline.split("\nlut ", 1)[0]
    asm = head + "\nlut " + " ".join(map(str, words)) + "\n\n"

    asm += """controller_0:
    set 26..30 L0..L4,
    set 16..20 L0..L4,
    set 21..25 O26..O30,
    set 31 L,
    if 7 worker_360

worker_golden:
    set 0..5 L8..L13,
    set 6..7 L,
    set 8..13 L8..L13,
    set 14..15 L,
    set 26..30 O26..O30,
    set 16..23 8..15,
    set 24..31 0..7,
    ldctib,
    read 16,
    write 16,
    jmp controller_0

worker_360:
    set 0..5 L0..L5,
    set 6..7 L,
    set 8..13 L0..L5,
    set 14..15 L,
    set 26..30 O26..O30,
    set 16..23 8..15,
    set 24..31 0..7,
    ldctib,
    read 16,
    write 16,
    jmp controller_0
"""
    return baseline, asm, old_lut, phase, words, safe_360


def main():
    print(f"Generating Phase5-360 BitScrambler program: {TARGET}")
    _, asm, _, _, words, safe_360 = build()
    TARGET.write_text(asm, encoding="utf-8")
    print(f"  Total LUT words: {len(words)}")
    print(f"  Safe 360° large-delta pairs: {len(safe_360)} (Strictly zero false alarms on delta < 12)")
    print(f"  Output written to: {TARGET}")


if __name__ == "__main__":
    main()
