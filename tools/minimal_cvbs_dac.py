#!/usr/bin/env python3
"""Estimate how little output hardware C5VRX can get away with.

The model generates a PAL-like composite waveform with sync, luma and a
4.43361875 MHz chroma component, quantizes it to a tiny GPIO resistor DAC, then
low-pass reconstructs it. It is intentionally an engineering sizing tool, not a
broadcast-compliance simulator.

It also evaluates a 1-bit first-order pulse-density path as the absolute minimum
(one GPIO + reconstruction network). That path is expected to be much noisier;
the point is to measure the trade-off instead of guessing.
"""
from __future__ import annotations

import argparse
import math

import numpy as np

PAL_CHROMA_HZ = 4_433_618.75
DEFAULT_FS = 40_000_000.0
DEFAULT_LOAD_OHM = 75.0
DEFAULT_VDD = 3.3
DEFAULT_FULL_SCALE_V = 1.0


def synthetic_cvbs(fs: float = DEFAULT_FS, lines: int = 8) -> np.ndarray:
    line_n = int(round(fs * 64e-6))
    sync_n = int(round(fs * 4.7e-6))
    active_start = int(round(fs * 10.4e-6))
    active_n = int(round(fs * 52.0e-6))

    out = np.full(line_n * lines, 0.30, dtype=np.float64)  # blank/black-ish pedestal
    for line in range(lines):
        base = line * line_n
        out[base:base + sync_n] = 0.0

        t = np.arange(active_n, dtype=np.float64) / fs
        # Slowly varying luma pattern plus a realistic color-subcarrier-sized term.
        x = np.linspace(0.0, 1.0, active_n, endpoint=False)
        luma = 0.38 + 0.42 * (0.5 + 0.5 * np.sin(2.0 * np.pi * (2.0 + 0.15 * line) * x))
        chroma_phase = (line & 1) * np.pi  # PAL-like alternating phase stress
        chroma = 0.12 * np.sin(2.0 * np.pi * PAL_CHROMA_HZ * t + chroma_phase)
        active = np.clip(luma + chroma, 0.30, 1.0)
        out[base + active_start:base + active_start + active_n] = active
    return out


def reconstruction_filter(x: np.ndarray, fs: float, pass_hz: float = 5.5e6, stop_hz: float = 7.0e6) -> np.ndarray:
    """Simple zero-phase frequency-domain reconstruction filter."""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(x.size, 1.0 / fs)
    h = np.ones_like(f)
    h[f >= stop_hz] = 0.0
    transition = (f > pass_hz) & (f < stop_hz)
    h[transition] = 0.5 * (1.0 + np.cos(np.pi * (f[transition] - pass_hz) / (stop_hz - pass_hz)))
    return np.fft.irfft(spec * h, n=x.size)


def quantize_uniform(x: np.ndarray, bits: int) -> np.ndarray:
    levels = (1 << bits) - 1
    return np.rint(np.clip(x, 0.0, 1.0) * levels) / levels


def sigma_delta_1bit(x: np.ndarray) -> np.ndarray:
    """First-order error-feedback PDM. Good enough to reject/accept the concept."""
    y = np.empty_like(x)
    integrator = 0.0
    previous = 0.0
    for i, v in enumerate(np.clip(x, 0.0, 1.0)):
        integrator += float(v) - previous
        bit = 1.0 if integrator >= 0.5 else 0.0
        y[i] = bit
        previous = bit
    return y


def metrics(reference: np.ndarray, candidate: np.ndarray) -> tuple[float, float, float]:
    err = candidate - reference
    signal = reference - np.mean(reference)
    mse = float(np.mean(err * err))
    snr = math.inf if mse <= 1e-30 else 10.0 * math.log10(float(np.mean(signal * signal)) / mse)
    corr = float(np.corrcoef(reference, candidate)[0, 1])
    rmse = math.sqrt(mse)
    return snr, corr, rmse


def direct_loaded_dac_resistors(bits: int,
                                vdd: float = DEFAULT_VDD,
                                full_scale_v: float = DEFAULT_FULL_SCALE_V,
                                load_ohm: float = DEFAULT_LOAD_OHM) -> list[float]:
    """Ideal binary-weight resistors for a directly terminated video input.

    Assumptions:
      * each GPIO is an ideal 0/VDD source;
      * the destination provides a fixed load to ground (normally 75 ohm);
      * code zero should be 0 V and all-ones should be full_scale_v.

    This deliberately does NOT claim 75-ohm source matching. It is the minimum
    short-trace/direct-input experiment. Long coax wants proper source matching.
    """
    if bits < 1 or full_scale_v <= 0 or full_scale_v >= vdd or load_ohm <= 0:
        raise ValueError("invalid DAC parameters")
    total_g = (full_scale_v / (vdd - full_scale_v)) / load_ohm
    unit_g = total_g / ((1 << bits) - 1)
    # Return MSB -> LSB because that is how people normally wire/document it.
    return [1.0 / (unit_g * (1 << k)) for k in reversed(range(bits))]


def evaluate(fs: float = DEFAULT_FS) -> dict[str, tuple[float, float, float]]:
    source = synthetic_cvbs(fs)
    ref = reconstruction_filter(source, fs)
    results: dict[str, tuple[float, float, float]] = {}

    one = reconstruction_filter(sigma_delta_1bit(source), fs)
    results["1-bit PDM"] = metrics(ref, one)

    for bits in range(2, 9):
        q = quantize_uniform(source, bits)
        rec = reconstruction_filter(q, fs)
        results[f"{bits}-bit DAC"] = metrics(ref, rec)
    return results


def self_test() -> None:
    r = evaluate(DEFAULT_FS)
    # The useful decision boundary: 2-bit is visibly coarse, 3-bit becomes a
    # plausible proof path, and >=5-bit should no longer dominate a healthy
    # short-range analog link in this synthetic test.
    assert r["3-bit DAC"][1] > 0.99, r
    assert r["3-bit DAC"][0] > 18.0, r
    assert r["5-bit DAC"][0] > 28.0, r
    assert r["1-bit PDM"][0] < r["3-bit DAC"][0], r
    print("minimal CVBS DAC model self-test PASS")
    for name, (snr, corr, rmse) in r.items():
        print(f"  {name:10s}: SNR {snr:5.1f} dB  corr {corr:.5f}  RMSE {rmse:.4f} V")

    for bits in (3, 4, 5, 6):
        values = direct_loaded_dac_resistors(bits)
        printable = ", ".join(f"{v:.0f} ohm" for v in values)
        print(f"  {bits}-bit direct/75R ideal resistors (MSB->LSB): {printable}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample-rate", type=float, default=DEFAULT_FS)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--bits", type=int, default=4, choices=range(2, 9))
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return 0

    r = evaluate(args.sample_rate)
    for name, (snr, corr, rmse) in r.items():
        print(f"{name:10s}: SNR {snr:5.1f} dB  corr {corr:.5f}  RMSE {rmse:.4f} V")

    resistors = direct_loaded_dac_resistors(args.bits)
    print(f"\nIdeal {args.bits}-bit short-trace resistor DAC into 75 ohm (MSB -> LSB):")
    for i, value in enumerate(resistors):
        print(f"  bit {args.bits - 1 - i}: {value:.1f} ohm")
    print("Model assumes the destination really terminates at 75 ohm and does not provide 75-ohm source matching.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
