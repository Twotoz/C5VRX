#!/usr/bin/env python3
"""Correlate ESP32-C5 MODEM_DIAG lanes against a high-resolution Q10/I10 capture.

Reference format: iq32le (Q10 bits 0..9, I10 bits 10..19).
DIAG format: little-endian uint32 per sample, lanes DIAG[0..19] in bits 0..19.

This tool deliberately discovers the mapping. It does not assume that
DIAG[0:9]=Q[0:9] and DIAG[10:19]=I[0:9]. It searches small sample lags,
reports the strongest bit match for each lane and can flag inversion.
"""
from __future__ import annotations

import argparse
import json
import random
import struct
import sys
from pathlib import Path


def read_u32(path: Path) -> list[int]:
    data = path.read_bytes()
    if len(data) % 4:
        raise ValueError(f"{path}: byte count is not divisible by four")
    return list(struct.unpack(f"<{len(data)//4}I", data))


def target_bit(word: int, target: int) -> int:
    if target < 10:
        return (word >> target) & 1
    return (word >> (10 + target - 10)) & 1


def target_name(target: int) -> str:
    return f"Q{target}" if target < 10 else f"I{target - 10}"


def score_mapping(
    reference80: list[int],
    diag40: list[int],
    decimation: int = 2,
    offset: int = 0,
    max_lag: int = 4,
    allow_invert: bool = True,
) -> list[dict]:
    ref = reference80[offset::decimation]
    results: list[dict] = []

    for lane in range(20):
        best = None
        for target in range(20):
            for lag in range(-max_lag, max_lag + 1):
                d0 = max(0, -lag)
                r0 = max(0, lag)
                n = min(len(diag40) - d0, len(ref) - r0)
                if n <= 32:
                    continue
                matches = 0
                for j in range(n):
                    a = (diag40[d0 + j] >> lane) & 1
                    b = target_bit(ref[r0 + j], target)
                    matches += a == b
                direct = matches / n
                inverted = 1.0 - direct
                use_invert = allow_invert and inverted > direct
                agreement = inverted if use_invert else direct
                candidate = {
                    "lane": lane,
                    "target": target_name(target),
                    "lag": lag,
                    "inverted": use_invert,
                    "agreement_permille": round(agreement * 1000.0, 3),
                    "samples": n,
                }
                if best is None or candidate["agreement_permille"] > best["agreement_permille"]:
                    best = candidate
        assert best is not None
        results.append(best)
    return results


def synthetic() -> tuple[list[int], list[int]]:
    rng = random.Random(0xD1A6)
    ref: list[int] = []
    for _ in range(6000):
        q = rng.randrange(1024)
        i = rng.randrange(1024)
        ref.append((i << 10) | q)

    dec = ref[0::2]
    lag = 2
    diag: list[int] = []
    for j in range(len(dec) - lag):
        word = dec[j + lag]
        q = word & 0x3FF
        i = (word >> 10) & 0x3FF
        diag.append(q | (i << 10))
    return ref, diag


def self_test() -> None:
    ref, diag = synthetic()
    result = score_mapping(ref, diag, decimation=2, offset=0, max_lag=4)
    for lane, row in enumerate(result):
        expected = f"Q{lane}" if lane < 10 else f"I{lane - 10}"
        assert row["target"] == expected, (lane, row)
        assert row["agreement_permille"] == 1000.0, (lane, row)
    print("MODEM_DIAG Q10 correlation oracle self-test passed")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("reference", nargs="?", type=Path, help="80 MS/s iq32le Q10/I10 capture")
    p.add_argument("diag", nargs="?", type=Path, help="40 MS/s packed DIAG[0..19] uint32 capture")
    p.add_argument("--decimation", type=int, default=2)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--max-lag", type=int, default=4)
    p.add_argument("--no-invert", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args(argv)

    if args.self_test:
        self_test()
        if args.reference is None and args.diag is None:
            return 0
    if args.reference is None or args.diag is None:
        p.error("reference and diag captures are required")

    rows = score_mapping(
        read_u32(args.reference), read_u32(args.diag),
        decimation=args.decimation, offset=args.offset,
        max_lag=args.max_lag, allow_invert=not args.no_invert)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            inv = " inverted" if r["inverted"] else ""
            print(f"DIAG[{r['lane']:2d}] -> {r['target']:>3s} "
                  f"score={r['agreement_permille']:7.3f}pm lag={r['lag']:+d}{inv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
