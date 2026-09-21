# RANGE V3 — phase-model range research

RANGE V3 is an opt-in receiver profile built on the existing Fusion/Range work.
It targets a specific failure mode: a high-gain Q4/I4 stream can still have
useful carrier energy while quantization/compression or phase-branch ambiguity
creates static and sync-destroying impulses.

It does **not** assume that high gain is bad. It also does not promote any
undocumented ESP32-C5 ROM control whose ABI or pre-Q4 effect has not been
measured.

## Live profile

The RF menu exposes `RANGE V3`.

V3 deliberately fixes:

- RF bandwidth: BW40;
- AFC: OFF / 0 kHz fine offset;
- initial sensitivity state: G62.

That isolates gain/quantization behavior from bandwidth and retune transients.

The original RANGE V2 classifier is unchanged. V3 adds a separate
`WEAK_DISTORTED` context.

### Soft compression metric

Hard clipping only counts exact Q4 rails (-8/+7). V3 additionally observes
non-hard samples touching +/-6 in either I or Q (`near_rail_permille`).

Near-rail occupancy alone never cuts gain. `WEAK_DISTORTED` also requires
phase evidence such as:

- strong-sample endpoint winding;
- PLL-lite slip evidence;
- robust-consensus outliers;
- Trajectory uncertainty.

This lets G62 remain the preferred weak-signal state when it is actually clean.
When amplitude remains usable but phase evidence says the state is distorted,
the optimizer may trial **one** lower-gain state. The existing risk/quality
acceptance then has to prove that state; otherwise it reverts.

`NO_CARRIER` still returns to G62.

## Why branch ambiguity is modeled instead of blindly rewrapping

For three 40 MS/s samples:

```text
p ---- m ---- c

d0 = wrap(phi[m] - phi[p])
d1 = wrap(phi[c] - phi[m])
raw_pair = d0 + d1
endpoint = wrap(phi[c] - phi[p])
```

If `raw_pair != endpoint`, the difference is normally one +/-2pi branch.
RANGE V3 treats that as an ambiguity with candidates:

```text
raw_pair - 2pi
raw_pair
raw_pair + 2pi
```

`tools/range_demod_bench.py` now contains a physics-gated branch experiment.
It repairs only an ambiguous event and only when either the envelope is weak or
the raw branch exceeds the configured FM-deviation prior. A predictor learned
from strong/plausible samples is used as a tie-breaker.

This remains offline until real captures prove that it improves hard-error
tails and semantic video. The live pixel path is still hardware paced.

## Real Q10 teacher

`tools/train_trajectory_v3.py` consumes the proven legacy `iq32le` dump format:

```text
Q10 = bits 0..9
I10 = bits 10..19
```

Default operation assumes the Q10 capture is 80 MS/s and derives the 40 MS/s
student stream by decimation-by-two. The Q4 student is generated from the same
Q10 words using the currently proven top nibble:

```text
Q4 = Q10[9:6]
I4 = I10[9:6]
```

The trainer compares high-resolution Q10 phase with Q4 phase and learns the
best {-2pi, 0, +2pi} correction for the same compact stage-1 address used by
Trajectory V2. It can emit a JSON branch/confidence model:

```sh
python3 tools/train_trajectory_v3.py capture.iq32le --json
python3 tools/train_trajectory_v3.py capture.iq32le --model-out model.json
```

A generated model is **not** automatically compiled into firmware. Promotion
requires held-out real captures and hardware A/B.

## MODEM_DIAG bit-map oracle

Only these live lanes are currently hardware-correlated:

```text
DIAG[6:9]   -> Q10[6:9]
DIAG[16:19] -> I10[6:9]
```

The attractive full-map hypothesis

```text
DIAG[0:9]   -> Q10[0:9]
DIAG[10:19] -> I10[0:9]
```

is therefore still a hypothesis.

`tools/modem_diag_q10_oracle.py` scores a captured 20-lane DIAG bus against a
Q10 reference, including a small sample-lag search and optional inversion:

```sh
python3 tools/modem_diag_q10_oracle.py q10.iq32le diag20.u32 --json
```

The result should be treated as proven only when repeated on hardware with
stable high agreement. Do not route a lower nibble as signed Q4 merely because
it toggles: a valid alternative slice must retain sign information or be
preceded by a measured scale/shift stage.

## FFT gain

Espressif exposes AGC and FFT scaling separately. C5VRX already has the
bounded `F` lab probe. RANGE V3 does not use FFT scaling unless that probe
demonstrates a same-boot change in the raw MODEM_DIAG Q4 statistics.

If Q4 is unchanged across FFT values, FFT scaling is downstream of the useful
tap for this receiver and should stay out of the range controller.

## Acceptance before any V3 demod becomes default

1. Capture Q10 at strong, medium and near-threshold RF levels and multiple gain
   states.
2. Train on a subset; validate on held-out captures.
3. Compare Golden, Trajectory V2, exact adjacent, V3 branch repair and PLL
   oracle on the identical Q4 data.
4. Track p95/p99 hard error, burst length, H-sync survival and decoder relocks.
5. Verify strong-signal video is not degraded.
6. Only then distill the winning phase model into a realtime BitScrambler
   contract and prove sustained transport on hardware.

RANGE V3 is intentionally a controlled experiment, not a range claim.
