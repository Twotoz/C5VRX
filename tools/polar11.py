#!/usr/bin/env python3
"""Generate and verify the Polar11 40 MS/s adjacent-FM pipeline.

Architecture:
    raw Q4/I4 @ 40 MS/s
      -> RX BitScrambler: raw8 -> nested Polar6 (Phase5 + 1 residual bit)
      -> 32 KiB one-byte/sample ring
      -> TX BitScrambler: prev Phase5 (5) + current Polar6 (6) -> 11-bit LUT
      -> one 6-bit DAC sample every 25 ns (40 MS/s unique CVBS)

The RX and TX BitScramblers are independent C5 engines and run in parallel.
TX steady state is one instruction bundle per input/output sample.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RX_BSASM = ROOT / "main" / "fm_rx_polar6.bsasm"
TX_BSASM = ROOT / "main" / "fm_polar11.bsasm"

TAU = 2.0 * math.pi
PEDESTAL = 20
CALIBRATION_GAIN = 2


def signed_bucket_center(code: int, bits: int) -> float:
    width = 1 << (10 - bits)
    center = code * width + (width - 1) * 0.5
    return center - 1024.0 if center >= 512.0 else center


def wrap(value: float) -> float:
    return (value + math.pi) % TAU - math.pi


def exact_phase(packed: int) -> float:
    q = signed_bucket_center(packed & 0x0F, 4)
    i = signed_bucket_center((packed >> 4) & 0x0F, 4)
    return math.atan2(q, i)


def phase5(packed: int) -> int:
    return round(exact_phase(packed) * 32.0 / TAU) & 0x1F


def polar6(packed: int) -> int:
    """Nested Phase5 + residual bit.

    Bits 5:1 are *exactly* the production Phase5 state.
    Bit 0 says which half of that Phase5 sector contains the raw-Q4 centroid.
    """
    coarse = phase5(packed)
    coarse_center = wrap(coarse * TAU / 32.0)
    residual = 1 if wrap(exact_phase(packed) - coarse_center) >= 0.0 else 0
    return (coarse << 1) | residual


def circular_centroids(codes: list[int], count: int) -> list[float]:
    result: list[float] = []
    for state in range(count):
        members = [exact_phase(packed) for packed in range(256)
                   if codes[packed] == state]
        if not members:
            raise AssertionError(f"empty polar state {state}")
        sine = sum(math.sin(value) for value in members)
        cosine = sum(math.cos(value) for value in members)
        result.append(math.atan2(sine, cosine))
    return result


P5 = [phase5(packed) for packed in range(256)]
P6 = [polar6(packed) for packed in range(256)]
P5_CENTROIDS = circular_centroids(P5, 32)
P6_CENTROIDS = circular_centroids(P6, 64)


def phase8_delta(previous: float, current: float) -> int:
    return round(wrap(current - previous) * 256.0 / TAU)


def scale_adjacent(value: int) -> int:
    """Map a 25 ns phase8 delta to the same CVBS scale as Golden's 50 ns path."""
    # Golden applies (gain+1)/4 to a 50 ns delta. Adjacent has half the phase
    # motion at the same FM deviation, so multiply the 25 ns delta by two first.
    numerator = (value * 2) * (CALIBRATION_GAIN + 1)
    if numerator < 0:
        return -((-numerator + 2) // 4)
    return (numerator + 2) // 4


def tx_address(previous_phase5: int, current_polar6: int) -> int:
    return (current_polar6 & 0x3F) | ((previous_phase5 & 0x1F) << 6)


def build_tx_lut() -> list[int]:
    lut = [PEDESTAL] * 2048
    for previous in range(32):
        for current in range(64):
            delta = phase8_delta(P5_CENTROIDS[previous], P6_CENTROIDS[current])
            code = max(0, min(63, PEDESTAL + scale_adjacent(delta)))
            lut[tx_address(previous, current)] = code
    return lut


def render_rx() -> str:
    lut_line = "lut " + " ".join(str(value) for value in P6)
    return f"""# Polar11 RX preprocessor: raw Q4/I4 -> nested Polar6 at 40 MS/s.
#
# Output byte bits:
#   5:1 = exact production Phase5 state
#   0   = within-Phase5 half-sector residual
#   7:6 = 0
#
# Hardware prefetch stays disabled because RX BitScrambler starts before
# PARLIO produces bytes. Eight ordinary reads warm M[63:0], then steady state
# is exactly one bundle per 25 ns input sample.
cfg prefetch false
cfg eof_on upstream
cfg trailing_bytes 0
cfg lut_width_bits 8
{lut_line}

prime_first:
    LDCTDA 0,
    read 8

fill_input:
    LOOPA 6 1 fill_input,
    read 8

prime_lookup:
    set 21..28 0..7,
    set 29..31 L,
    read 8

convert:
    set 0..7 L0..L7,
    set 21..28 0..7,
    set 29..31 L,
    read 8,
    write 8,
    jmp convert
"""


def render_tx() -> str:
    lut = build_tx_lut()
    lut_line = "lut " + " ".join(str(value) for value in lut)
    return f"""# Polar11: one-bundle 40 MS/s adjacent-FM TX backend.
#
# Input ring: one nested Polar6 byte per 25 ns from fm_rx_polar6.bsasm.
# Address: current Polar6[5:0] + previous Phase5[4:0] = 11 bits.
# LUT8 directly returns the calibrated six-bit adjacent-FM DAC code.
#
# O8..O12 keeps previous Phase5. In stream, RHS O8..O12 is the old state
# while bits 8..12 are simultaneously replaced by current Polar6[5:1].
# Thus one bundle performs output + next address + state update + read + write.
cfg prefetch true
cfg eof_on downstream
cfg trailing_bytes 0
cfg lut_width_bits 8
{lut_line}

prime_previous:
    # First Polar6 sample becomes previous Phase5; no output yet.
    set 8..12 1..5,
    read 8

prime_address:
    # Address interval sample0 -> sample1 and advance state to sample1.
    set 8..12 1..5,
    set 21..26 0..5,
    set 27..31 O8..O12,
    read 8

stream:
    # Emit previous lookup while issuing the next adjacent lookup.
    # This is the entire 25 ns steady-state datapath: ONE bundle/sample.
    set 0..5 L0..L5,
    set 6..7 L,
    set 8..12 1..5,
    set 21..26 0..5,
    set 27..31 O8..O12,
    read 8,
    write 8,
    jmp stream
"""


def rms_delta_error(use_polar11: bool) -> tuple[float, float]:
    errors: list[float] = []
    for previous_raw in range(256):
        pq = signed_bucket_center(previous_raw & 0x0F, 4)
        pi = signed_bucket_center(previous_raw >> 4, 4)
        if pi * pi + pq * pq <= 128.0 * 128.0:
            continue
        for current_raw in range(256):
            cq = signed_bucket_center(current_raw & 0x0F, 4)
            ci = signed_bucket_center(current_raw >> 4, 4)
            if ci * ci + cq * cq <= 128.0 * 128.0:
                continue
            reference = wrap(exact_phase(current_raw) - exact_phase(previous_raw))
            if use_polar11:
                estimate = wrap(P6_CENTROIDS[P6[current_raw]] -
                                P5_CENTROIDS[P5[previous_raw]])
            else:
                estimate = wrap(P5_CENTROIDS[P5[current_raw]] -
                                P5_CENTROIDS[P5[previous_raw]])
            error = math.degrees(wrap(estimate - reference))
            errors.append(error)
    rms = math.sqrt(sum(value * value for value in errors) / len(errors))
    peak = max(abs(value) for value in errors)
    return rms, peak


def self_test(check_generated: bool = True) -> None:
    assert len(P6) == 256
    assert set(P6) == set(range(64))
    for packed in range(256):
        assert (P6[packed] >> 1) == P5[packed]

    tx = build_tx_lut()
    assert len(tx) == 2048
    assert min(tx) == 0 and max(tx) == 63
    assert len(set(tx)) >= 32

    p5_rms, p5_peak = rms_delta_error(False)
    p11_rms, p11_peak = rms_delta_error(True)
    assert p11_rms < p5_rms
    assert p11_peak < p5_peak

    rx_source = render_rx()
    tx_source = render_tx()
    assert "cfg lut_width_bits 8" in rx_source
    assert "cfg lut_width_bits 8" in tx_source
    stream = tx_source.split("stream:", 1)[1]
    assert stream.count("read 8") == 1
    assert stream.count("write 8") == 1
    assert "set 21..26 0..5" in stream
    assert "set 27..31 O8..O12" in stream

    if check_generated:
        if RX_BSASM.read_text(encoding="utf-8") != rx_source:
            raise AssertionError("fm_rx_polar6.bsasm drift; run tools/polar11.py --write")
        if TX_BSASM.read_text(encoding="utf-8") != tx_source:
            raise AssertionError("fm_polar11.bsasm drift; run tools/polar11.py --write")

    print("Polar11 self-test PASS")
    print("  raw8 -> nested Polar6: 256/256, all 64 states used")
    print("  nested invariant: Polar6>>1 == production Phase5")
    print("  TX address: 5+6 = 11 bits, 2048-entry LUT8")
    print("  TX steady state: exactly 1 bundle / 25 ns = 2 bundles / 50 ns")
    print(f"  Phase5 pair error: {p5_rms:.3f} deg RMS / {p5_peak:.3f} deg max")
    print(f"  Polar11 pair error: {p11_rms:.3f} deg RMS / {p11_peak:.3f} deg max")


def write_generated() -> None:
    RX_BSASM.write_text(render_rx(), encoding="utf-8")
    TX_BSASM.write_text(render_tx(), encoding="utf-8")
    print(f"wrote {RX_BSASM.relative_to(ROOT)}")
    print(f"wrote {TX_BSASM.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.write:
        write_generated()
    if args.self_test or not args.write:
        self_test(check_generated=True)


if __name__ == "__main__":
    main()
