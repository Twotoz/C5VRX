# Golden-Transport-V2 Architecture, Pedestal 25 Autopsy, and Parity Findings

## Executive Summary

This document consolidates the architectural freeze of **Golden-Transport-V2**, the autopsy of the Pedestal 25 experiment, the empirical findings of the **Golden-Odd vs. Golden-Even (A/B 1)** parity test, and the exact physical impedance calculations governing analog reconstruction filtering.

---

## 1. Golden-Transport-V2 Architecture & Layered Separation

To prevent compounding regressions and isolate bugs to a single layer, the video receiver architecture is permanently divided into three decoupled layers:

```text
┌─────────────────────────────────────────────────────────────┐
│ 1. ACQUISITION LAYER (100% "Dumb", Lossless Transport)      │
│    MODEM_DIAG Q4/I4 (Full 8-bit packed IQ, no truncation)   │
│    ──► PARLIO RX @ 40 MS/s (POS clock edge, stock cyclic)    │
│    ──► 32 KiB RAW IQ RING (HP SRAM cyclic transport buffer) │
│    Strict Rule: No DSP, no WBFM knowledge, no hot-path copy │
└──────────────────────────────┬──────────────────────────────┘
                               │ 40 MB/s pure data (all samples preserved)
┌──────────────────────────────▼──────────────────────────────┐
│ 2. DEMODULATOR LAYER (Replaceable Plugin Interface)          │
│    Production Baseline: Golden Phase5 (20 MS/s info rate)   │
│    - 50 ns coherent single-parity span                      │
│    - Centroid-calibrated polar LUT (pedestal 20, gain 2)     │
│    - Downstream EOF, trailing_bytes = 0                     │
│    - BitScrambler persistent register state across wraps    │
└──────────────────────────────┬──────────────────────────────┘
                               │ 20 MS/s CVBS info rate
┌──────────────────────────────▼──────────────────────────────┐
│ 3. TRANSPORT & OUTPUT LAYER                                  │
│    ──► [D, D] sample duplication to 40 MS/s PARLIO TX       │
│    ──► 6-bit weighted resistor ladder DAC                   │
│    ──► 75 Ω terminated CVBS analog output                   │
└─────────────────────────────────────────────────────────────┘
```

### Runtime Pipeline Build Stamp
To ensure absolute visibility over active firmware parameters, `c5vrx2_realtime_start()` prints an explicit runtime build stamp before muting startup logging:
```text
=======================================================
 PIPELINE STAMP: [GOLDEN-TRANSPORT-V2]
 RX:            40 MS/s POS edge, stock cyclic GDMA
 Ring:          32768 bytes (HP SRAM)
 Demod:         Phase5 (ODD parity: s1, s3, s5...) / (EVEN parity: s0, s2, s4...)
 Calibration:   Pedestal=20, Gain=2
 TX:            40 MS/s [D,D] 6-bit resistor DAC
 BitScrambler:  EOF downstream, trailing 0, persistent state
 Telemetry:     OFF (hot path clean)
=======================================================
```

---

## 2. Autopsy of the Pedestal 25 Experiment (Option A)

### Rationale & Hypothesis
In `scratch/measure_pedestal_clipping.py`, running Golden Phase5 on `vtx_real_capture_v3.bin` revealed that with `pedestal = 20`, **5.16%** of samples clipped flat at DAC code 0 due to 75 µs pre-emphasis overshoot. Increasing pedestal to 25 dropped bottom clipping to **1.61%**.

### Live Empirical Result
Live testing of `build-golden-ped25-notel` on Fat Shark Dominator V1 goggles resulted in **immediate regression**:
* Noticeable increase in desync issues and horizontal line glitches.
* Picture stability significantly worse than Golden 32K baseline.

### Root Cause Diagnosis
1. **Sync Slicer Threshold Compression**:
   Analog composite video (CVBS) uses sync tips at 0 V to establish the DC reference via an AC-coupling capacitor and sync-tip clamp diode in the monitor/goggles.
2. **Elevated Floor**:
   Raising the digital pedestal by +5 DAC codes lifted the sync pulses upward from code 0 towards code 5.
3. **Clamp Confusion**:
   The goggle's analog sync slicer threshold (~50% between blanking and sync tip) suffered threshold ambiguity and DC baseline wander, triggering false horizontal and vertical sync slicing.
4. **Verdict**: `pedestal = 20` is strictly required to preserve the analog sync-to-blanking voltage step.

---

## 3. A/B 1 Empirical Test: Golden-Odd vs. Golden-Even

### Methodology
To test whether MODEM_DIAG exhibits parity-dependent sampling asymmetry or corruption on one of the two 20M lanes, bit-for-bit identical BitScrambler programs were generated:
* **Golden-Odd** (`c5vrx2_wbfm_q4_phase5_2to1.bsasm`): Selects bits `8..15` ($s_1, s_3, s_5, \dots$).
* **Golden-Even** (`c5vrx2_wbfm_q4_phase5_even_2to1.bsasm`): Selects bits `0..7` ($s_0, s_2, s_4, \dots$).

Every other parameter—LUT values, cycle cadence, ring size (32 KiB), clock edge (POS), and DAC output (`[D, D]`)—remained 100% identical.

### Live Observation
* **Live Hardware Result**: The Even build exhibited comparable behavior to the Odd build (same baseline static, desync rate, and image characteristics).

### Key Takeaways
1. **Strong Exoneration of Both 20M Substreams**:
   Neither parity lane is uniquely broken or corrupted. MODEM_DIAG delivers valid RF baseband data into both the even and odd bytes of the 32 KiB HP SRAM ring.
2. **Jitter Not From Single-Lane Defect**:
   The remaining static and periodic desync observed in live video is not caused by selecting the wrong byte lane.
3. **Caveat**:
   While both 20M substreams are individually healthy, this does not yet guarantee that the 40M combined sequence has 100% sample-perfect phase continuity across time; boundary wraps and long-run integrity must still be verified in A/B 2.

---

## 4. Physical Impedance Calculation & Analog Filtering

### Exact Equivalent Impedance at the `VIDEO` Pin
The XIAO ESP32-C5 resistor DAC network consists of:
1. **Series Resistors from GPIOs**:
   $R_{ladder} = (1/8200 + 1/3900 + 1/2000 + 1/1000 + 1/470 + 1/240)^{-1} \approx 122\,\Omega$.
2. **Shunt Resistor**:
   $R_{shunt} = 200\,\Omega$ to GND.
3. **Receiver Termination**:
   $R_{term} = 75\,\Omega$ inside the Fat Shark Dominator V1 goggles.

The total parallel equivalent resistance at the `VIDEO` node is:
$$R_{eq} = 122\,\Omega \parallel 200\,\Omega \parallel 75\,\Omega = \mathbf{37.7\,\Omega}$$

### Analysis of the 560 pF Board Capacitor
With $R_{eq} = 37.7\,\Omega$ and $C = 560\text{ pF}$, the 1st-order cutoff frequency is:
$$f_c = \frac{1}{2\pi \cdot 37.7\,\Omega \cdot 560\times 10^{-12}\,\text{F}} = \mathbf{7.54\text{ MHz}}$$

* **At 10 MHz (Nyquist / 50 ns edge transitions)**:
  Attenuation is only $-4.4\text{ dB}$ (60% of the step energy passes unattenuated).
* **Conclusion**: 7.54 MHz is too high to round off the 50 ns staircase hold (`[A, A]`). This explains why 50 ns "kartels" remain visible even with the 560 pF capacitor installed.
* **Filter Boundary Note**: While a higher capacitance (~1 nF) lowers $f_c$ to ~4.2 MHz, values above 680 pF risk encroaching on PAL/NTSC color burst and high-frequency luma resolution. Analog filter alterations remain an independent, deferred evaluation.

---

## 5. Frozen Roadmap

```text
Golden-Transport-V2 (Frozen Reference)
        │
        ├── [COMPLETED] A/B 1: Golden-Odd vs. Golden-Even
        │   └── Result: Both 20M lanes strongly exonerated, symmetrical live behavior.
        │
        ├── [NEXT] A/B 2: Long-run raw/ring integrity
        │   └── Objective: Measure GDMA descriptor boundary wraps and verify 
        │       continuity across 32 KiB HP SRAM wraps to resolve periodic desync.
        │
        └── Acquisition FREEZE
                    │
                    ├── DSP: Onderzoeken van de luminantie-afhankelijke rainbow/chroma spur
                    ├── DSP: Transfer curve tuning & discriminator optimization
                    └── OUTPUT: Kartels & reconstruction evaluation
```
