#!/usr/bin/env python3
"""Deterministic host oracle for the C5VRX Alpha predictive adjacent demod."""

from __future__ import annotations

import argparse
import math
import random
from typing import Sequence

TAU = 2.0 * math.pi
PEDESTAL = 20
CONF_POWER_MIN = 16
MILD_INNOVATION = 8
MEDIUM_INNOVATION = 28
MAX_LOWCONF_STEP = 24


def iround(x: float) -> int:
    return int(math.floor(x + 0.5)) if x >= 0.0 else -int(math.floor(-x + 0.5))


def s4(v: int) -> int:
    v &= 15
    return v - 16 if v & 8 else v


def bucket_center(code: int) -> float:
    value = code * 64.0 + 31.5
    return value - 1024.0 if value >= 512.0 else value


def phase5(raw: int) -> int:
    q = bucket_center(raw & 15)
    i = bucket_center((raw >> 4) & 15)
    return iround(math.atan2(q, i) * 32.0 / TAU) & 31


PHASE5 = [phase5(raw) for raw in range(256)]


def raw_high_confidence(raw: int) -> bool:
    q = s4(raw & 15)
    i = s4((raw >> 4) & 15)
    both_rails = i in (-8, 7) and q in (-8, 7)
    return i * i + q * q >= CONF_POWER_MIN and not both_rails


def signed_delta(previous: int, current: int) -> int:
    delta = (current - previous) & 31
    return delta - 32 if delta >= 16 else delta


def map_pair_steps(pair_steps: int) -> int:
    phase8_sum = pair_steps * 8
    numerator = phase8_sum * 3
    correction = -((-numerator + 2) // 4) if numerator < 0 else (numerator + 2) // 4
    return max(0, min(63, PEDESTAL + correction))


def alpha_tracker_code(pair_bias: int, state: int, high_confidence: bool) -> int:
    observation = map_pair_steps(max(0, min(62, pair_bias)) - 32)
    if high_confidence:
        return observation

    prediction = min(63, (state & 7) * 8 + 4)
    innovation = observation - prediction
    magnitude = abs(innovation)
    if magnitude <= MILD_INNOVATION:
        corrected = observation
    elif magnitude <= MEDIUM_INNOVATION:
        step = (magnitude + 1) // 2
        corrected = prediction + (step if innovation > 0 else -step)
    else:
        corrected = prediction + max(-MAX_LOWCONF_STEP,
                                     min(MAX_LOWCONF_STEP, innovation))
    return max(0, min(63, corrected))


def build_lut() -> list[int]:
    words: list[int] = []
    for address in range(1024):
        phase = PHASE5[address & 0xFF]
        previous = address & 31
        current = (address >> 5) & 31
        biased_delta = signed_delta(previous, current) + 16

        pair_bias = address & 63
        state = (address >> 6) & 7
        high = bool((address >> 9) & 1)
        code = alpha_tracker_code(pair_bias, state, high)

        if address < 256:
            wanted = int(raw_high_confidence(address))
            if (code & 1) != wanted:
                code ^= 1

        words.append(phase | (biased_delta << 5) | (code << 10))
    return words


LUT = build_lut()


def adjacent_pair(previous_raw: int, sample0: int, sample1: int) -> int:
    d0 = signed_delta(PHASE5[previous_raw], PHASE5[sample0])
    d1 = signed_delta(PHASE5[sample0], PHASE5[sample1])
    return map_pair_steps(d0 + d1)


def alpha_pair(previous_raw: int, sample0: int, sample1: int,
               state: int) -> tuple[int, int]:
    previous = LUT[previous_raw] & 31
    phase0 = LUT[sample0] & 31
    phase1 = LUT[sample1] & 31
    d0_bias = (LUT[previous | (phase0 << 5)] >> 5) & 31
    d1_bias = (LUT[phase0 | (phase1 << 5)] >> 5) & 31
    pair_bias = d0_bias + d1_bias
    high = ((LUT[sample0] >> 10) & 1) & ((LUT[sample1] >> 10) & 1)
    address = pair_bias | ((state & 7) << 6) | (high << 9)
    code = (LUT[address] >> 10) & 63
    return code, code >> 3


def quantize(phi: float, amplitude: float, sigma: float,
             rng: random.Random) -> int:
    i = amplitude * math.cos(phi) + rng.gauss(0.0, sigma)
    q = amplitude * math.sin(phi) + rng.gauss(0.0, sigma)
    ii = max(-8, min(7, math.floor(i + 0.5))) & 15
    qq = max(-8, min(7, math.floor(q + 0.5))) & 15
    return (ii << 4) | qq


def map_pair_rad(rad: float) -> int:
    phase8 = iround(rad * 256.0 / TAU)
    n = phase8 * 3
    correction = -((-n + 2) // 4) if n < 0 else (n + 2) // 4
    return max(0, min(63, 20 + correction))


def synthetic_case(amplitude: float, sigma: float,
                   count: int = 20000) -> dict[str, float]:
    rng = random.Random(0xA1FA)
    phase = 0.0
    raw: list[int] = []
    truth: list[float] = []
    for k in range(count):
        inst = (0.72 * math.sin(TAU * k / 71.0) +
                0.34 * math.sin(TAU * k / 19.0))
        phase = (phase + inst + math.pi) % TAU - math.pi
        truth.append(inst)
        raw.append(quantize(phase, amplitude, sigma, rng))

    adjacent_ge16 = alpha_ge16 = 0
    adjacent_ge32 = alpha_ge32 = 0
    adjacent_abs = alpha_abs = 0
    pairs = 0
    state = 2

    for end in range(2, len(raw), 2):
        previous, middle, current = end - 2, end - 1, end
        target = map_pair_rad(truth[middle] + truth[current])
        adjacent = adjacent_pair(raw[previous], raw[middle], raw[current])
        alpha, state = alpha_pair(raw[previous], raw[middle], raw[current], state)
        adjacent_error = abs(adjacent - target)
        alpha_error = abs(alpha - target)
        adjacent_ge16 += adjacent_error >= 16
        alpha_ge16 += alpha_error >= 16
        adjacent_ge32 += adjacent_error >= 32
        alpha_ge32 += alpha_error >= 32
        adjacent_abs += adjacent_error
        alpha_abs += alpha_error
        pairs += 1

    return {
        "pairs": float(pairs),
        "adjacent_ge16_pm": 1000.0 * adjacent_ge16 / pairs,
        "alpha_ge16_pm": 1000.0 * alpha_ge16 / pairs,
        "adjacent_ge32_pm": 1000.0 * adjacent_ge32 / pairs,
        "alpha_ge32_pm": 1000.0 * alpha_ge32 / pairs,
        "adjacent_mae": adjacent_abs / pairs,
        "alpha_mae": alpha_abs / pairs,
    }


def self_test() -> None:
    assert len(LUT) == 1024
    for raw in range(256):
        assert (LUT[raw] & 31) == PHASE5[raw]
        assert ((LUT[raw] >> 10) & 1) == int(raw_high_confidence(raw))

    # High-confidence Alpha is deliberately transparent: previous state cannot
    # change the exact-adjacent output.
    for pair_bias in range(63):
        expected = map_pair_steps(pair_bias - 32)
        for state in range(8):
            address = pair_bias | (state << 6) | (1 << 9)
            assert ((LUT[address] >> 10) & 63) == expected

    # Low-confidence catastrophic innovation is bounded before emission.
    for state in range(8):
        prediction = min(63, state * 8 + 4)
        for pair_bias in range(63):
            address = pair_bias | (state << 6)
            output = (LUT[address] >> 10) & 63
            observation = map_pair_steps(pair_bias - 32)
            if abs(observation - prediction) > MEDIUM_INNOVATION:
                assert abs(output - prediction) <= MAX_LOWCONF_STEP + 1

    clean = synthetic_case(6.0, 0.35)
    weak = synthetic_case(2.5, 1.0)

    assert clean["alpha_ge16_pm"] <= clean["adjacent_ge16_pm"] + 0.2
    assert weak["alpha_ge16_pm"] < weak["adjacent_ge16_pm"] * 0.75
    assert weak["alpha_ge32_pm"] < weak["adjacent_ge32_pm"] * 0.50
    assert weak["alpha_mae"] < weak["adjacent_mae"]

    print(
        "Alpha self-test passed: "
        f"clean_ge16={clean['alpha_ge16_pm']:.2f}pm "
        f"weak_ge16 adjacent={weak['adjacent_ge16_pm']:.1f}pm "
        f"alpha={weak['alpha_ge16_pm']:.1f}pm "
        f"weak_ge32 adjacent={weak['adjacent_ge32_pm']:.1f}pm "
        f"alpha={weak['alpha_ge32_pm']:.1f}pm "
        f"mae adjacent={weak['adjacent_mae']:.2f} alpha={weak['alpha_mae']:.2f}"
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if args.self_test:
        self_test()
    else:
        self_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
