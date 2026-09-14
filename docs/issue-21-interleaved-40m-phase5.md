# Issue #21: Interleaved 40 MS/s Phase5 Architecture & Silicon Pipeline Proof

## 1. Executive Summary & Experimental Evidence

This document consolidates all physical findings and measurements from recent live testing on the Seeed Studio XIAO ESP32-C5 (rev v1.0) paired with Fat Shark Dominator V1 goggles, detailing the transition from the 20 MS/s baseline to the true, uncompromised 40 MS/s Interleaved Phase5 architecture.

### Key Experimental Discoveries:
1. **Issue #12 Acquisition Clock Matrix Elimination**:
   - Tested 4 physical combinations: Internal 40 MHz POS, Internal 40 MHz NEG, PLL_F40M on GPIO2 POS, PLL_F40M on GPIO2 NEG.
   - All 4 valid clock configurations yielded the exact same video, kartels (tanden), and layer-twitching.
   - MODEM DEBUG_CLK40 via DIAG21 produced only static (lane 21 does not provide a continuous 40 MHz clock).
   - **Conclusion**: The acquisition timing / clock edge is not the bottleneck; the source of artifacts lies in digital CVBS reconstruction and DAC cadence.

2. **Negative Piecewise Discriminator Failure Mode**:
   - A piecewise sync-protection LUT (reducing negative slope from 0.75 to 0.25 and clamping noise < -34 to pedestal 18) was tested live.
   - **Result**: Immediate severe regression ("static en kartels zijn veel erger").
   - **Physics**: Compressing the negative slope distorted the negative half-cycle of the 3.58 MHz NTSC color subcarrier, causing severe 2nd harmonic distortion, chroma phase collapse, and aggressive color/edge tearing. The step-discontinuity at -34 generated loud static clicks.
   - **Rule**: The FM discriminator transfer function must remain strictly linear across both positive and negative deviations ($20 + \Delta \times 0.75$) to preserve chroma fidelity and noise floor.

3. **Rejection of Midpoint $(A+B)/2$ Interpolation**:
   - Midpoint interpolation $[A, (A+B)/2, B, (B+C)/2]$ invents artificial intermediate data. While cosmetically smoothing luma, it distorts chroma phase and fast FM transitions.
   - An external capacitor creates an exponential RC low-pass filter (not a linear interpolator); 560 pF with short leads was physically tested and showed no visible improvement.
   - Midpoint interpolation in BitScrambler requires $\ge 3$ bundles (75 ns) or 12-bit LUT addressing (4096 entries), both physically impossible on ESP32-C5.

---

## 2. The 40 -> 40 -> 40 MS/s Architectural Law

### The Problem in Baseline Golden Phase5
```text
MODEM ADC @ 40 MS/s
   s0     s1     s2     s3     s4     s5
          │             │             │
          └── φ1 ───────└── φ3 ───────└── φ5     (Odd bytes only: 20 MS/s)
              
                 φ3 - φ1       φ5 - φ3
                    ↓             ↓
CVBS DAC:         [A, A]        [B, B]           (50 ns flat hold @ 40 MHz)
```
- Every middle sample ($s_0, s_2, s_4, \dots$) is discarded.
- The DAC receives identical byte pairs $[A, A]$, creating a 50 ns zero-order hold staircase that appears as "kartels" (tanden) on analog goggles.

---

## 3. The Two-Stage Interleaved 40 MS/s Architecture

The true, uncompromised solution processes **every single RF sample** and emits **every 25 ns a distinct DAC update**, while keeping the exact 50 ns discriminator span that gives Golden Phase5 its low static floor:

```text
MODEM ADC @ 40 MS/s
   s0     s1     s2     s3     s4     s5  ... (all samples)
    │      │      │      │      │      │
 ┌────────────────────────────────────────┐
 │ STAGE 1: RX BitScrambler (1 bundle)    │
 │ Full 8-bit Q4/I4 -> 5-bit atan2 Phase  │
 └────────────────────────────────────────┘
    │      │      │      │      │      │
   φ0     φ1     φ2     φ3     φ4     φ5  @ 40 MS/s Phase Ring
    │      │      │      │      │      │
    │      └──────┼──────┐      │      │
    │             │      │      │      │
 ┌────────────────────────────────────────┐
 │ STAGE 2: TX BitScrambler (1 bundle/s)  │
 │ Even: φk - φk-2 (50 ns span)          │
 │ Odd:  φk - φk-2 (50 ns span)          │
 └────────────────────────────────────────┘
    │      │      │      │      │      │
    ↓      ↓      ↓      ↓      ↓      ↓
   D0     D1     D2     D3     D4     D5  @ 40 MS/s to DAC (25 ns updates)
```

### Mathematical Properties:
1. **Zero discarded samples**: $100\%$ of modem ADC data is utilized.
2. **Continuous 25 ns updates**: Every DAC output is distinct $[D_0, D_1, D_2, \dots]$, completely eliminating the 50 ns flat hold $[A, A]$.
3. **50 ns discriminator span preserved**:
   - $H_{even}(z) = 1 - z^{-2}$
   - $H_{odd}(z) = 1 - z^{-2}$
   - The factor $(1 + z^{-1})$ maintains the transmission zero (boxcar notch) at $f = 20\text{ MHz}$, suppressing high-frequency triangular FM noise.
4. **Full 8-bit Cartesian precision**: The first stage maps all 256 Cartesian $Q4/I4$ states to 5-bit polar angle. Zero bit-slicing error.

---

## 4. Silicon Feasibility Proof (ESP32-C5 BitScrambler)

Verification of ESP-IDF 6.0.1 and ESP32-C5 hardware register headers (`bitscrambler_ll.h`) proves:
1. **Independent Dual Cores**:
   - `hw->lut_cfg[BITSCRAMBLER_DIR_RX]` and `hw->lut_cfg[BITSCRAMBLER_DIR_TX]` are **physically separate LUT memories** (each 1024 entries / 2 KiB).
   - `hw->inst_cfg[BITSCRAMBLER_DIR_RX]` and `hw->inst_cfg[BITSCRAMBLER_DIR_TX]` are **physically separate instruction RAMs** (each up to 8 instructions).
2. **Stage 1 (RX BitScrambler) fits in 1 Bundle**:
   ```bsasm
   prime:
       set 16..23 0..7,
       set 24..25 L,
       read 8

   loop:
       set 0..7 L0..L7,
       set 16..23 0..7,
       set 24..25 L,
       read 8,
       write 8,
       jmp loop
   ```
   - Compiled and assembled with `/opt/esp/idf/tools/bsasm.py`: **1 bundle per byte** (25 ns per byte = 40 MB/s continuous throughput).
3. **Stage 2 (TX BitScrambler) fits in 1 Bundle/Sample (2 Bundles for 2 Samples)**:
   ```bsasm
   prime_even:
       set 8..12 0..4,
       read 8

   prime_odd:
       set 26..30 0..4,
       set 16..20 0..4,
       set 21..25 O8..O12,
       set 31 L,
       read 8

   step_even:
       set 0..7 L0..L7,
       set 8..12 0..4,
       set 16..20 0..4,
       set 21..25 O26..O30,
       set 31 L,
       read 8,
       write 8

   step_odd:
       set 0..7 L0..L7,
       set 26..30 0..4,
       set 16..20 0..4,
       set 21..25 O8..O12,
       set 31 L,
       read 8,
       write 8,
       jmp step_even
   ```
   - Uses non-overlapping registers: `0..7` (DAC write), `8..12` (`prev_even`), `16..25` (LUT address), `26..30` (`prev_odd`).
   - Compiled and assembled with `/opt/esp/idf/tools/bsasm.py`: **Exactly 2 bundles for 2 distinct samples = 1 bundle/sample = 25 ns = 40 MS/s**.
   - LUT size: exactly $32 \times 32 = 1024$ entries, matching hardware LUT capacity perfectly.
