# Issue #23 & Architecture: Knowledge Consolidation and Road Map

## Status

Branch: `codex/issue-23-architecture-and-knowledge-pr`
Date: 2026-09-15

---

## 1. Hardware Result: RX-BitScrambler Ingress Route Rejected for Production

### Hypothesis tested (Candidate F / commit `be4e3a9`)

```text
MODEM_DIAG Q4/I4 @40M
-> PARLIO RX
-> RX BitScrambler (WBFM premapper)
-> phase ring
-> PARLIO TX @20M
```

### Result: Hardware failure — sync never locked

**Observed**: Full-screen static; static patterns tracked camera panning (RF energy passing through), but zero HSYNC/VSYNC lock.  
**Physical mechanism**: MODEM_DIAG pushes raw 8-bit samples at exactly 40.000 MB/s with zero flow control. In contrast to TX where GDMA pulls from SRAM with backpressure through the TX FIFO, an RX-attached BitScrambler must absorb every incoming byte synchronously. In live hardware tests, the RX-attached BitScrambler could not sustain this continuous input cadence, dropping bytes and shattering the nanosecond-precise NTSC sync timebase. (The lack of an official ESP-IDF `parlio_rx_unit_decorate_bitscrambler()` helper further confirms that RX decoration is not a standard, synchronized path in IDF).

**Source**: `docs/continuous-iq-findings.md` §"Direct TX-BitScrambler WBFM proof":
> *"The RX-attached BitScrambler could not sustain the required input cadence,
> so the realtime topology now stores raw Q4/I4 with PARLIO RX and decorates
> the PARLIO TX transaction instead."*

**Architectural conclusion**:

> **PARLIO RX must write raw Q4/I4 directly to SRAM via stock ESP-IDF cyclic GDMA.
> Do not pursue RX-BitScrambler ingress as the production route without new hardware evidence.**

This rule is enforced in `tools/validate_continuous_pipeline.py`:
```python
assert "s_rx_bs" not in realtime
```

---

## 2. Proven Negative: Modulo-256 Pair-Sum = Endpoint Discriminator

### The mathematical identity

The `q4_2to1.bsasm` ADDCTIAL pipeline computes:

```text
acc = 0
acc += phase(s1)            // wrap to 8 bits
acc += -phase(s0_prev)      // same accumulator mod 256
acc += phase(s2)
acc += -phase(s1)
result = acc mod 256        // = (phase(s2) - phase(s0_prev)) mod 256
```

This is **identical** to the endpoint discriminator `(phase(s2) - phase(s0)) mod 256`.

**Concrete proof**:
```text
p0 =   0°, p1 = +100°, p2 = +200°

Adjacent signed:
  d0 = wrap(p1 - p0) = +100
  d1 = wrap(p2 - p1) = +100
  correct pair-sum    = +200   ← preserves winding

8-bit accumulator:
  100 + 100 = 200 mod 256 = -56 as signed 8-bit

Endpoint discriminator:
  wrap(200 - 0) = -56          ← same!
```

**Consequence**: Any ADDCTIAL-based approach using an 8-bit accumulator provides
zero additional winding information vs. the current Golden Phase5. The phase5_adj
approach (modulo-256 centroid-phase8 ADDCTIAL pair-sum) was therefore removed.

**To actually fix winding**, adjacent deltas must be summed in ≥9 bits *before*
saturation/mapping. This cannot be done with the BitScrambler ALU within 2 bundles.
The trajectory LUT (Issue #9) addresses this correctly by consulting the middle
sample index in a pre-trained 3-byte LUT.

---

## 3. Proven: DMA Wrap Bug and Its Fix (Golden-Transport-V2)

### Root cause discovered in hardware

`cfg trailing_bytes 9` + `cfg eof_on upstream` discarded 9 bytes (225 ns) at every
cyclic DMA wrap. One NTSC color subcarrier cycle = 279.4 ns → the discard produced
a ~290° chroma discontinuity and a violent horizontal sync jump at every wrap.

### Fix (proven in hardware — best live image to date)

```asm
cfg eof_on downstream
cfg trailing_bytes 0
```

In `address_phase`:
```asm
set 26..30 O26..O30,   ; retain phase state across wraps
```

PARLIO TX `loop_transmission=true` never generates a downstream EOF, so the
BitScrambler runs uninterrupted through all cyclic ring wraps.

**This fix is applied to**:
- `main/c5vrx2_wbfm_q4_phase5_2to1.bsasm` (Golden baseline)
- `main/c5vrx2_wbfm_q4_trajectory_2to1.bsasm` (fixed in this PR — was still
  using `eof_on upstream / trailing_bytes 9`)

---

## 4. Proven: Why Winding Matters (Issue #23 Core Claim)

### Measurement on real capture (`vtx_real_capture_v3.bin`)

- Endpoint `n→n+2` winding loss: **8.35%** of all 50 ns intervals
- Same metric on strong-IQ subset (amplitude² ≥ 64): **0.285%**

### What a wrong branch means

One wrong winding branch ≈ full ±2π error. After production gain/clamp this
becomes a large CVBS excursion rather than a few codes of noise.

Example: if the true phase path is 0° → +110° → +220°:
- **Endpoint** gives `wrap(220° - 0°) = -140°` — **wrong branch**
- **Adjacent** gives `+110° + +110° = +220°` — **correct** (only if not re-wrapped)

### Which live symptoms this explains

```text
wrong endpoint branch
  -> large discriminator excursion
  -> malformed CVBS sample(s) near sync timing
  -> H-sync edge corruption
  -> horizontal layer shift
  -> intermittent black bar / vertical jump
  -> worse at distance/weak RF (IQ magnitude ↓ → winding errors ↑)
```

### Confirmed by Golden Even/Odd A/B test

Both parities (odd s1,s3,s5… and even s0,s2,s4…) produced comparable image
quality. This proves the problem is **not** a fixed parity lane — both 20M
substreams are equally affected. This is consistent with a demodulator problem
that affects all samples, not a hardware asymmetry.

---

## 5. Trajectory LUT: Mathematical Autopsy and the Confidence-Aware Architecture

### What the Trajectory LUT Address Actually Does

The 3-instruction trajectory pipeline uses an address constructed from:
```text
address = phase4(prev) | (middle_QI_signs << 4) | (phase4(curr) << 6)
```
This indexes a 1024-entry LUT. It groups the input triples by endpoint phase4 bins and uses the middle sample's quadrant/sign bits as a classification feature.

### The Mathematical Flaw in `tools/train_trajectory_lut.py`

In the current repository, `train_trajectory_lut.py` computes:
```python
endpoint = wrap_r(PHI[c] - PHI[p])
adjacent = wrap_r(PHI[m] - PHI[p]) + wrap_r(PHI[c] - PHI[m])
branch = np.rint((adjacent - endpoint) / (2 * np.pi)).astype(int)
target = scale(np.rint(wrap_r(adjacent) * 256 / (2 * np.pi)).astype(int))
fallback = scale(np.rint(endpoint * 256 / (2 * np.pi)).astype(int))
```

Notice line 26:
```python
target = scale(np.rint(wrap_r(adjacent) * 256 / (2 * np.pi)).astype(int))
```

By applying `wrap_r(adjacent)`, the training target **re-wraps the adjacent sum back into $[-\pi, +\pi)$**.
Because:
$$\text{wrap}_r(\text{wrap}_r(\phi_m - \phi_p) + \text{wrap}_r(\phi_c - \phi_m)) \equiv \text{wrap}_r(\phi_c - \phi_p) = \text{endpoint}$$

**Exhaustive verification across all 16,777,216 possible Q4/I4 triples:**
- `target` vs `fallback` differ in only **1,984 out of 16,777,216 cases** (**0.0118%**).
- In **99.9882% of cases, `target` is mathematically identical to `fallback` (the endpoint)**.

Therefore, the claim that the checked-in trajectory LUT recovers winding is practically false for this trainer. The LUT trained almost entirely against the endpoint principal branch.
The measured offline 12.9% MAE improvement was achieved by quantization, grouping, and address distribution averaging, **not** by true branch/winding disambiguation.

This directly explains the live hardware test result of `trajectory_notel`:
- Sync issues persist
- Difficulty maintaining color burst lock
- Side kartels and static still present

### Why Blind Unwrapped Adjacent is ALSO Fatal

One might naively conclude that removing `wrap_r` and using purely unwrapped adjacent deltas:
$$\Delta = \text{wrap}_r(\phi_m - \phi_p) + \text{wrap}_r(\phi_c - \phi_m)$$
would solve the problem. **It does not.**

Frozen capture analysis (`vtx_real_capture_v3.bin`) demonstrates:
- All intervals winding disagreement: **8.351%**
- Strong-IQ intervals ($\text{amplitude}^2 \ge 64$): **0.285%**

Over **96% of all apparent winding disagreements occur at weak IQ / near-origin samples**. At near-origin vectors, the phase angle is dominated by RF noise and quantization jitter. A small noise vector crossing the origin generates an artificial $\pm 2\pi$ phase orbit (noise click).

If a demodulator blindly accepts unwrapped adjacent deltas, it treats every noise click as a full $\pm 2\pi$ discriminator excursion. This injects rail-to-rail impulses into the CVBS stream, explaining why Candidates G/H and True40 suffered from massive active-video static and desync.

### The Correct Demodulator Architecture: Confidence-Aware Branch Correction

The demodulator must balance endpoint stability against genuine winding:

```text
                    middle trajectory
                           │
                           ▼
Golden endpoint ──► branch decision ◄── IQ confidence
                           │
                           ▼
                        CVBS
```

1. **Golden Endpoint is the Default**: At strong IQ, endpoint and adjacent agree >99.7% of the time. Endpoint avoids noise-click amplification.
2. **Middle Trajectory as Branch Disambiguator**: The middle sample should only override the principal branch when the observed trajectory exhibits high confidence and physical consistency.
3. **IQ Confidence Awareness**: Near-origin samples must not trigger branch unwrapping. Phase clicks must be suppressed, not amplified.

### Transport Bug Fix in `main/c5vrx2_wbfm_q4_trajectory_2to1.bsasm`

Independent of the DSP LUT training, the trajectory assembly previously had `cfg eof_on upstream / trailing_bytes 9` (the 225 ns wrap discard bug). That transport bug is fixed in this PR to `cfg eof_on downstream / trailing_bytes 0`.

---

## 6. What Has Been Hardware-Proven Negative (Do Not Repeat)

| Approach | Why rejected | Reference |
|----------|-------------|-----------|
| RX-BitScrambler WBFM | No `parlio_rx_unit_decorate_bitscrambler()`, sample drops | `continuous-iq-findings.md` |
| Seamless RX register hack | Floating enable, descriptor race → dirty static | issue-22, commit `c06519d` revert |
| 128 KiB ring | Linker: collides with MAC dump SRAM 0x4082ffc0..0x4085003f | issue-22 §4 |
| 4092 vs 4096 DMA geometry (PR #8) | Hardware A/B: unchanged or worse, layers thicker | issue-22 §"PR8" |
| CPU atan2/FIR at 40 MS/s | No CPU budget for floating-point at IQ sample rate | `continuous-iq-findings.md` |
| INVALID_STATE → pedestal (PR5) | Sacrificed valid phase state, marked ~12% of capture invalid | issue-9, wbfm_q4.c comment |
| Naive phase4 direct scaler | Collapses to ~7 DAC levels (posterisation) | `docs/issue-9-trajectory.md` |
| `q4_2to1` ADDCTIAL pair-sum | Mathematically = endpoint discriminator mod 256 | This document §2 |
| Pedestal 25 | Worse sync, more glitches (compresses sync tip) | Session findings |
| 100 ns discriminator | Not a solution for winding; different mechanism | Prior sessions |
| Candidates G/H interleaved | Very static, desynced; clock-phase skew between odd/even | Hardware test |

---

## 7. Current Production Baseline

**Golden-Transport-V2** (`build-golden-32k-notel`):

```text
MODEM_DIAG Q4/I4 @40M
  -> PARLIO RX 40 MHz POS edge (stock ESP-IDF cyclic GDMA)
  -> 32 KiB raw IQ ring (HP SRAM)
  -> PARLIO TX 40 MHz (loop_transmission)
  -> TX BitScrambler (Golden Phase5)
     cfg eof_on downstream, trailing_bytes 0, persistent phase state
     2-bundle steady-state: address_delta -> emit
     Embedded 1024×16-bit LUT (Phase5 + centroid delta → 6-bit DAC)
     Pedestal 20, Gain 2, Current-minus-previous
     Output: [D,D] @40 MS/s
  -> 6-bit resistor DAC
```

**Known remaining issues** (not transport):
- ~8.35% endpoint winding errors at typical RF (demodulator limitation)
- 64.4° chroma subcarrier phase step from 50 ns [D,D] ZOH
- Dark-scene rainbow/color overlay (confirmed phase step mechanism)
- Rare black bar / vertical jump (origin under investigation)

---

## 8. Next Steps (Prioritized)

### Phase 1: Validate Trajectory LUT on Hardware (Ready to test)

The trajectory bsasm is now transport-fixed (`eof_on downstream`, `trailing_bytes 0`).

**Build command**:
```powershell
docker run --rm -v "${PWD}:/workspace" -w /workspace espressif/idf:v6.0.2 `
  idf.py -B build-trajectory-notel -D SDKCONFIG_DEFAULTS="/workspace/sdkconfig.trajectory_notel" build
```

**A/B test**: Compare vs. `build-golden-32k-notel` on same hardware/scene.

**What to measure**:
- Horizontal layer shifts (primary symptom of wrong winding branch)
- Static / speckles
- Sync lock stability
- Dark-scene color behavior

### Phase 2: Fix Trainer and Train Confidence-Aware Trajectory LUT on Real Captures

Before training a new LUT, `tools/train_trajectory_lut.py` must be upgraded:
1. **Remove `wrap_r(adjacent)` in target calculation**: Do not fold the adjacent trajectory sum back into the endpoint principal branch.
2. **Implement Confidence Gating**: Evaluate IQ magnitude ($\text{amplitude}^2 = Q^2 + I^2$). Only allow the middle sample trajectory to override the endpoint branch when IQ magnitude is above threshold ($\ge 64$).
3. **Train on Real Captures**:
```bash
python tools/train_trajectory_lut.py \
  --train measurements/issue-11-cvbs/vtx_real_capture_v3.bin \
  --write
```
4. Rebuild and test on hardware.

### Phase 3: DAC Linearity Characterization

Before further DSP improvements, measure the actual DAC transfer curve:
- Output all 64 DAC codes to the 75Ω load
- Measure actual voltage per code
- Characterize DNL/INL/monotonicity
- Rainbow artifacts may partly be DAC nonlinearity, not just DSP

### Phase 4: Long-Run Transport Oracle

Verify that ring wrap boundaries are truly seamless with a scope/logic-analyzer:
- Known test pattern into PARLIO RX
- Compare output across ≥1000 wrap boundaries
- Look for missing samples, duplicates, or phase resets

---

## 9. Architecture Decision: No RX-BS Ingress Replacement

The PR brief requested investigating:
```text
MODEM_DIAG Q4/I4 @40M
-> PARLIO RX -> RX-BitScrambler -> CVBS ring -> PARLIO TX @20M
```

**Decision**: Do not implement. Hardware-proven negative (see §1).

The simplification goal is valid, but the path to achieve it requires either:
1. A future ESP-IDF version with a hardware-synchronized RX BitScrambler decorator, or
2. A CPU-side M2M processing loop (not proven sustainable at 40 MB/s), or
3. An ETM/DMA-triggered pipeline not yet explored.

The current production hot path (raw IQ ring → TX-BS) is the only hardware-proven
approach. Focus should be on improving DSP quality within the TX-BS constraints.

---

## 10. Winding-Correct Adjacent FM: Mathematical & Physical Requirements

A true adjacent FM implementation that avoids winding loss **must**:

1. Compute $d_0 = \text{signed\_wrap}(\phi_{n+1} - \phi_n)$ as a signed value.
2. Compute $d_1 = \text{signed\_wrap}(\phi_{n+2} - \phi_{n+1})$ as a signed value.
3. Compute trajectory sum $d_0 + d_1$ in **at least 9 bits** without intermediate wrapping.
4. **Gate by IQ confidence**: If IQ magnitude is weak (near origin), default to the principal endpoint delta $\text{wrap}(\phi_{n+2} - \phi_n)$ to avoid amplifying noise clicks.
5. Apply gain/pedestal/clamp to the final result.

This cannot be done within the BitScrambler's 8-bit ADDCTIAL accumulator at runtime.

**The only viable 2-bundle approach** is the confidence-aware trajectory LUT:
Pre-compute the optimal branch decision offline using the middle sample and IQ confidence, embed it into the 1024-entry LUT, and address it at runtime via 3-byte indexing.

**Not viable without new hardware proof**:
- M2M (unproven sustained throughput at 40 MB/s)
- RX-BitScrambler (hardware-proven failure)
- CPU DSP (no budget at 40 MS/s)
- More than 2 TX-BitScrambler bundles (causes TX FIFO underrun → black screen)
