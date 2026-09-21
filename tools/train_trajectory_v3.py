#!/usr/bin/env python3
"""Train/inspect the RANGE V3 phase-branch model from real ESP32-C5 Q10 IQ.

Input is the legacy C5VRX iq32le format:
  bits  0.. 9: signed Q10
  bits 10..19: signed I10

The MODEM_DIAG live path exposes Q4/I4 at 40 MS/s. The default teacher path
therefore decimates the 80 MS/s Q10 capture by two and derives exactly the
corresponding signed top-nibble Q4/I4 representation. High-resolution Q10
phase is the teacher; Q4 is the student observation.

The output model is intentionally supervisory/offline. It is not silently
promoted to the realtime BitScrambler until real-capture A/B proves it.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import struct
import sys
from pathlib import Path

from range_demod_bench import PHASE5, TAU, phase_rad, wrap


def sign10(v: int) -> int:
    v &= 0x3FF
    return v - 0x400 if v & 0x200 else v


def unpack_word(word: int) -> tuple[int, int]:
    q = sign10(word)
    i = sign10(word >> 10)
    return i, q


def q4_byte_from_word(word: int) -> int:
    q10 = word & 0x3FF
    i10 = (word >> 10) & 0x3FF
    return (((i10 >> 6) & 0x0F) << 4) | ((q10 >> 6) & 0x0F)


def phase10(word: int) -> float:
    i, q = unpack_word(word)
    return math.atan2(float(q), float(i))


def nearest_branch(truth: float, observed: float) -> int:
    choices = (-1, 0, 1)
    return min(choices, key=lambda k: abs((observed + k * TAU) - truth))


def percentile_abs(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(abs(v) for v in values)
    idx = int(round((len(xs) - 1) * p))
    return xs[max(0, min(len(xs) - 1, idx))]


def read_words(path: Path) -> list[int]:
    data = path.read_bytes()
    if len(data) % 4:
        raise ValueError(f"{path}: iq32le byte count is not divisible by four")
    return list(struct.unpack(f"<{len(data)//4}I", data))


def train(
    words80: list[int],
    decimation: int = 2,
    offset: int = 0,
    parity: int = 1,
) -> tuple[dict, dict]:
    if decimation < 1:
        raise ValueError("decimation must be >= 1")
    if not 0 <= offset < decimation:
        raise ValueError("offset must be inside decimation period")

    words = words80[offset::decimation]
    if len(words) < 16:
        raise ValueError("capture too short")

    counts = [[0, 0, 0] for _ in range(1024)]
    teacher_abs: list[float] = []
    observed_abs: list[float] = []
    corrections = 0
    records = 0

    q4 = [q4_byte_from_word(w) for w in words]
    p10 = [phase10(w) for w in words]

    for end in range(parity + 2, len(words), 2):
        p, m, c = end - 2, end - 1, end
        truth = wrap(p10[m] - p10[p]) + wrap(p10[c] - p10[m])
        observed = (
            wrap(phase_rad(q4[m]) - phase_rad(q4[p])) +
            wrap(phase_rad(q4[c]) - phase_rad(q4[m]))
        )
        branch = nearest_branch(truth, observed)
        if branch:
            corrections += 1

        addr = (
            q4[c]
            | (((q4[m] >> 7) & 1) << 8)
            | (((PHASE5[q4[p]] >> 4) & 1) << 9)
        )
        counts[addr][branch + 1] += 1
        teacher_abs.append(truth)
        observed_abs.append(observed)
        records += 1

    model: list[int] = []
    confidence: list[int] = []
    correct = 0
    covered = 0
    branch_hist = {-1: 0, 0: 0, 1: 0}
    for row in counts:
        total = sum(row)
        if not total:
            model.append(0)
            confidence.append(0)
            continue
        covered += 1
        best_idx = max(range(3), key=lambda i: row[i])
        best_branch = best_idx - 1
        model.append(best_branch)
        confidence.append((row[best_idx] * 255) // total)
        correct += row[best_idx]
        branch_hist[best_branch] += row[best_idx]

    stats = {
        "records": records,
        "covered_stage1_states": covered,
        "teacher_branch_correction_permille": 1000.0 * corrections / max(1, records),
        "stage1_branch_model_accuracy_permille": 1000.0 * correct / max(1, records),
        "teacher_pair_abs_p99_rad": percentile_abs(teacher_abs, 0.99),
        "q4_pair_abs_p99_rad": percentile_abs(observed_abs, 0.99),
        "majority_branch_histogram": branch_hist,
        "decimation": decimation,
        "offset": offset,
        "parity": parity,
    }
    return {"branch": model, "confidence": confidence}, stats


def synthetic_words(n: int = 100_000) -> list[int]:
    rng = random.Random(0xC503)
    phase = 0.0
    out: list[int] = []
    for k in range(n):
        inst = 0.18 * math.sin(TAU * k / 173.0) + 0.07 * math.sin(TAU * k / 41.0)
        phase = wrap(phase + inst)
        fade = (k // 1700) % 5 == 4
        amp = 85.0 if fade else 315.0
        sigma = 28.0 if fade else 10.0
        i = amp * math.cos(phase) + 14.0 + rng.gauss(0.0, sigma)
        q = amp * 1.06 * math.sin(phase) - 9.0 + rng.gauss(0.0, sigma)
        ii = max(-512, min(511, int(round(i)))) & 0x3FF
        qq = max(-512, min(511, int(round(q)))) & 0x3FF
        out.append((ii << 10) | qq)
    return out


def self_test() -> None:
    assert sign10(0x3FF) == -1
    assert sign10(0x200) == -512
    assert q4_byte_from_word(((0x3C0) << 10) | 0x040) == 0xF1
    assert nearest_branch(-1.2, TAU - 1.2) == -1
    model, stats = train(synthetic_words())
    assert len(model["branch"]) == 1024
    assert len(model["confidence"]) == 1024
    assert stats["records"] > 10_000
    assert stats["covered_stage1_states"] > 100
    assert stats["stage1_branch_model_accuracy_permille"] >= 333.0
    print("trajectory_v3 Q10 teacher self-test passed")
    print(json.dumps(stats, sort_keys=True))


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("capture", nargs="?", type=Path)
    p.add_argument("--decimation", type=int, default=2)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--parity", choices=("even", "odd"), default="odd")
    p.add_argument("--model-out", type=Path)
    p.add_argument("--json", action="store_true")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args(argv)

    if args.self_test:
        self_test()
        if args.capture is None:
            return 0
    if args.capture is None:
        p.error("capture is required unless --self-test is used")

    model, stats = train(
        read_words(args.capture),
        decimation=args.decimation,
        offset=args.offset,
        parity=1 if args.parity == "odd" else 0,
    )
    if args.model_out:
        payload = {"format": "c5vrx-range-v3-branch-model-v1", "stats": stats, **model}
        args.model_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    if args.json:
        print(json.dumps(stats, indent=2, sort_keys=True))
    else:
        for key, value in stats.items():
            print(f"{key:42s} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
