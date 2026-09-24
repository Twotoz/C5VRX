#!/usr/bin/env python3
"""Offline falsification probe for a confidence-aware PolarState8 LUT.

No live firmware is changed by this script. The clean FM trajectory is the
teacher; only the Q4 observation is corrupted. Its learned state is limited to
the two writable LUT bits; the third state bit is always the raw-I sign.
"""

import math
import random

from build_polarstate8 import cvbs, state2, word


def q4(value):
    return max(-8, min(7, math.floor(value + 0.5)))


def loss(output, target):
    e = abs(output - target)
    return e + 4 * (e >= 8) + 20 * (e >= 16) + 100 * (e >= 32)


def next_state(state, raw, hold_weak):
    i = ((raw >> 4) & 15) - (16 if raw & 0x80 else 0)
    q = (raw & 15) - (16 if raw & 0x08 else 0)
    learned = state & 3 if hold_weak and i * i + q * q <= 2 else state2(raw)
    return learned | (((raw >> 7) & 1) << 2)


def trajectory(seed, count, amplitude, noise):
    rng = random.Random(seed)
    phase = rng.random() * 2 * math.pi
    for tick in range(count):
        # Baseband motion plus NTSC-like 3.58 MHz chroma; periodic genuine
        # reversals deliberately challenge any blanket output smoothing.
        step = (0.11 * math.sin(tick * 0.00061)
                + 0.18 * math.sin(tick * 0.0061)
                + 0.14 * math.sin(tick * 2 * math.pi * 3.58 / 40))
        if tick % 2003 in (0, 1, 2):
            step = -0.65 if tick % 2 else 0.65
        phase += step
        amp = amplitude * (0.25 if tick % 4096 in range(200, 260) else 1.0)
        i = q4(amp * math.cos(phase) + rng.gauss(0, noise))
        q = q4(amp * math.sin(phase) + rng.gauss(0, noise))
        yield ((i & 15) << 4) | (q & 15), cvbs(2 * step)


def train(hold_weak):
    counts = [[0] * 64 for _ in range(2048)]
    for amp, noise, seed in ((6, 0.35, 11), (6, 0.8, 12),
                             (4, 0.8, 13), (3, 1.2, 14)):
        state = 0
        for raw, teacher in trajectory(seed, 140_000, amp, noise):
            counts[(state << 8) | raw][teacher] += 1
            state = next_state(state, raw, hold_weak)

    lut = bytearray(2048)
    for address, histogram in enumerate(counts):
        state, raw = address >> 8, address & 255
        seed_dac = word(state, raw) & 63
        if sum(histogram) < 16:
            dac = seed_dac
        else:
            # Minimize the requested tail-aware loss, not the conditional
            # mean. Weak regularization prevents sparse cells from railing.
            dac = min(range(64), key=lambda out: (
                sum(n * loss(out, target) for target, n in enumerate(histogram))
                + 5 * abs(out - seed_dac), abs(out - seed_dac)))
        lut[address] = dac | ((next_state(state, raw, hold_weak) & 3) << 6)
    return lut, sum(bool(sum(h)) for h in counts)


def evaluate(lut, hold_weak, amp, noise, seed):
    state = 0
    score = square = tail = rails = cov = target_var = output_var = pedestal = 0
    for raw, teacher in trajectory(seed, 50_000, amp, noise):
        address = (state << 8) | raw
        output = (word(state, raw) if lut is None else lut[address]) & 63
        score += loss(output, teacher)
        square += (output - teacher) ** 2
        tail += abs(output - teacher) >= 16
        rails += output in (0, 63)
        pedestal += output == 20
        cov += (output - 20) * (teacher - 20)
        target_var += (teacher - 20) ** 2
        output_var += (output - 20) ** 2
        state = next_state(state, raw, hold_weak)
    return tuple(round(x, 3) for x in
                 (score / 50_000, math.sqrt(square / 50_000),
                  tail / 50_000, rails / 50_000, pedestal / 50_000,
                  cov / target_var, math.sqrt(output_var / target_var)))


def main():
    for hold in (False, True):
        lut, covered = train(hold)
        print(f"two-bit hold={hold} covered={covered}/2048")
        print("  metrics=(tail-loss, RMS, >=16, rails, pedestal, slope, RMS transfer)")
        for amp, noise in ((6, 0), (6, 0.35), (6, 0.8), (4, 0.8), (3, 1.2)):
            old = evaluate(None, hold, amp, noise, 100 + amp)
            new = evaluate(lut, hold, amp, noise, 100 + amp)
            print(f"  amp={amp} noise={noise}: seed={old} trained={new}")


if __name__ == "__main__":
    main()
