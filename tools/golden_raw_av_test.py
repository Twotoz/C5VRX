#!/usr/bin/env python3
"""Deterministic RF -> WBFM -> calibrated six-bit CVBS golden test."""

from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np

RF_HZ = 80_000_000
CVBS_HZ = 20_000_000
LINE = 1280
FSC = 4_433_618.75
RESISTORS = np.array([8200., 3900., 2000., 1000., 470., 240.])


def dac_voltages() -> np.ndarray:
    conductance = 1 / RESISTORS
    denominator = conductance.sum() + 1 / 200 + 1 / 75
    return np.array([
        (3.3 * conductance[((code >> np.arange(6)) & 1).astype(bool)].sum())
        / denominator for code in range(64)
    ])


DAC = dac_voltages()


def composite(lines: int = 10) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    signal = np.full(lines * LINE, 0.3, np.float64)
    masks = {name: np.zeros(signal.size, bool) for name in
             ("sync", "blank", "active", "burst")}
    for line in range(lines):
        base = line * LINE
        signal[base:base + 94] = 0.0
        masks["sync"][base:base + 94] = True
        masks["blank"][base + 170:base + 205] = True
        t = np.arange(108, 160) / CVBS_HZ
        signal[base + 108:base + 160] += 0.15 * np.sin(2*np.pi*FSC*t +
                                                       (line & 1) * np.pi)
        masks["burst"][base + 108:base + 160] = True
        x = np.linspace(0, 1, 1040, endpoint=False)
        luma = 0.32 + 0.48*x
        chroma = 0.12*np.sin(2*np.pi*FSC*np.arange(1040)/CVBS_HZ + 0.7)
        active = luma + chroma
        active[300:360] = 0.32
        active[680:740] = 0.95
        signal[base + 210:base + 1250] = active
        masks["active"][base + 210:base + 1250] = True
    return signal, masks


def fm_iq(cvbs: np.ndarray, amplitude: float, dc: complex) -> np.ndarray:
    source = np.repeat(cvbs, 4)
    # Four adjacent increments span at most ~0.65 rad: ample wrap margin.
    increment = 0.02 + (source - 0.3) * 0.85 / 4
    phase = np.cumsum(increment)
    iq = amplitude*np.exp(1j*phase) + dc
    i = np.clip(np.rint(iq.real), -512, 511).astype(np.int16)
    q = np.clip(np.rint(iq.imag), -512, 511).astype(np.int16)
    return i.astype(np.float64) + 1j*q.astype(np.float64)


def exact_discriminator(iq: np.ndarray) -> np.ndarray:
    centered = iq - iq.mean()
    # Same dphi4 contract as firmware: x[4k] * conj(x[4k-4]).
    kept = centered[::4]
    return np.angle(kept[1:] * np.conj(kept[:-1]))


def coarse_phase(iq: np.ndarray) -> np.ndarray:
    dc = iq.mean()
    i = np.clip(np.rint(iq.real - dc.real), -512, 511).astype(np.int16)
    q = np.clip(np.rint(iq.imag - dc.imag), -512, 511).astype(np.int16)
    i5 = ((i.astype(np.uint16) & 0x3ff) >> 5) & 0x1f
    q5 = ((q.astype(np.uint16) & 0x3ff) >> 5) & 0x1f
    def center(code: np.ndarray) -> np.ndarray:
        signed = np.where(code & 0x10, code.astype(int) - 32, code.astype(int))
        return signed*32 + 15.5
    phase = np.mod(np.rint(np.arctan2(center(q5), center(i5))*256/(2*np.pi)),
                   256).astype(np.uint8)
    kept = phase[::4].astype(np.int16)
    delta = ((kept[1:] - kept[:-1] + 128) & 0xff) - 128
    return delta * (2*np.pi/256)


def calibrate(raw: np.ndarray, masks: dict[str, np.ndarray]) -> tuple[np.ndarray, float, float]:
    valid_masks = {key: value[1:] for key, value in masks.items()}
    sync = float(np.median(raw[valid_masks["sync"]]))
    blank = float(np.median(raw[valid_masks["blank"]]))
    gain = 18.0 / (blank - sync)
    bias = -sync*gain
    codes = np.clip(np.rint(raw*gain + bias), 0, 62).astype(np.uint8)
    return codes, gain, bias


def wrapped_error(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.angle(np.exp(1j*(a-b)))


def run_case(amplitude: float) -> dict[str, float]:
    cvbs, masks = composite()
    iq = fm_iq(cvbs, amplitude, 75 - 48j)
    exact = exact_discriminator(iq)
    coarse = coarse_phase(iq)
    error = wrapped_error(coarse, exact)
    codes, _, _ = calibrate(coarse, masks)
    reference_codes, _, _ = calibrate(exact, masks)
    voltage = DAC[codes]
    reference_voltage = DAC[reference_codes]
    active = masks["active"][1:]
    sync = masks["sync"][1:]
    blank = masks["blank"][1:]
    burst = masks["burst"][1:]
    burst_a = voltage[burst] - voltage[blank].mean()
    burst_b = reference_voltage[burst] - reference_voltage[blank].mean()
    phase = np.angle(np.vdot(burst_b, burst_a))
    expected_edges = np.flatnonzero(np.diff(sync.astype(np.int8)) != 0)
    observed_edges = np.flatnonzero(np.diff((codes <= 3).astype(np.int8)) != 0)
    timing_error = max(min(abs(int(edge) - observed_edges))
                       for edge in expected_edges)
    result = {
        "phase_error_rms": float(np.sqrt(np.mean(error**2))),
        "phase_error_max": float(np.max(np.abs(error))),
        "discriminator_error_rms": float(np.sqrt(np.mean((coarse-exact)**2))),
        "sync_timing_error_samples": float(timing_error),
        "sync_amplitude_error_v": float(abs(voltage[sync].mean() -
                                             reference_voltage[sync].mean())),
        "blank_amplitude_error_v": float(abs(voltage[blank].mean() -
                                              reference_voltage[blank].mean())),
        "active_video_rms_error_v": float(np.sqrt(np.mean(
            (voltage[active] - reference_voltage[active])**2))),
        "burst_amplitude_error_v": float(abs(np.sqrt(np.mean(burst_a**2)) -
                                               np.sqrt(np.mean(burst_b**2)))),
        "burst_phase_error_rad": float(abs(phase)),
        "minimum_code": int(codes.min()),
        "maximum_code": int(codes.max()),
    }
    assert result["phase_error_rms"] < (0.20 if amplitude < 200 else 0.08)
    assert result["discriminator_error_rms"] < (0.25 if amplitude < 200 else 0.10)
    assert result["sync_timing_error_samples"] <= 2
    assert result["sync_amplitude_error_v"] < (0.10 if amplitude < 200 else 0.03)
    assert result["blank_amplitude_error_v"] < (0.13 if amplitude < 200 else 0.05)
    assert result["active_video_rms_error_v"] < (0.23 if amplitude < 200 else 0.06)
    assert result["minimum_code"] == 0 and result["maximum_code"] <= 62
    return result


def replay(path: Path) -> None:
    words = np.fromfile(path, dtype="<u4")
    if words.size < 8 or words.size % 4:
        raise ValueError("real IQ replay must contain a multiple of four u32 words")
    q = (words & 0x3ff).astype(np.int16)
    i = ((words >> 10) & 0x3ff).astype(np.int16)
    q = ((q.astype(np.int32) + 512) % 1024 - 512)
    i = ((i.astype(np.int32) + 512) % 1024 - 512)
    exact = exact_discriminator(i + 1j*q)
    coarse = coarse_phase(i + 1j*q)
    error = wrapped_error(coarse, exact)
    print(f"REAL_REPLAY words={words.size} phase_error_rms={np.sqrt(np.mean(error**2)):.6f} "
          f"phase_error_max={np.max(np.abs(error)):.6f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-iq", type=Path)
    args = parser.parse_args()
    for amplitude, label in ((450., "STRONG_RF"), (120., "LOW_AMPLITUDE_RF")):
        metrics = run_case(amplitude)
        print(label, " ".join(f"{key}={value:.6f}" if isinstance(value, float)
                              else f"{key}={value}" for key, value in metrics.items()))
    if args.real_iq:
        replay(args.real_iq)
    else:
        print("REAL_REPLAY skipped=no_retained_PR6_PR9_binary_fixture")
    assert abs(DAC[18] - 0.296817) < 1e-6
    assert abs(DAC[62] - 1.002317) < 1e-6
    print("C5VRX_RAW_AV_GOLDEN result=PASS iq_layout=Q10_I10 timebase_hz=80000000 decimation=4 dac=EXACT_LADDER")


if __name__ == "__main__":
    main()
