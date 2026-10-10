# Picture-cliff study: why the whole picture drops out (2026-10-10)

Extends C5VRX by Twotoz and the C5VRX contributors (span50/HC50, RANGE32,
PAIR, EDGE and the untrained-PAIR experiment). Synthetic engineering
measurements only: no board run, no measured RF range, no goggle acceptance.

## Problem statement

Operator: "as soon as the video is only a little bad, the complete video
drops out". A goggle shows no picture when its sync separator / H-PLL loses
lock, regardless of how good the luma is. Earlier screens never measured that:
they counted pulses on ~13-line snippets, on the fine lane, with a centred and
fitted carrier. The board runs fixed ultrafine lanes, AFC/AutoFit has never
fitted there (`fit=0` in every board log), and VTXs differ in deviation.

`tools/dsp_search/goggle_lock.py` therefore models the goggle stage:
5 MHz input filter, 1 MHz sync low-pass, width-checked slicer, H-PLL flywheel
(+-1.5 us window, gain 0.3), lock lost below 16 of 32 lines and regained after
8 consecutive syncs. Two AGC variants: `ideal` (slicer fixed at -20 IRE of a
per-demod clean calibration) and `peak` (diode-style sync-tip clamp, instant
attack, 2 ms release - a cheap goggle). A line counts when the goggle is
locked and its start is within 0.5 us of the true sync (constant delay
removed). `cliff_study.py` runs full PAL/NTSC fields per case.

Conditions: fixed ultrafine lane, RMS 3.5-5 cells, deviation 0.6-1.4
(operator VTX measured ~0.69), DC/IQ error, four picture patterns.
`CLIFF_BOARD=1`: carrier +-1 MHz off centre and the nominal pre-fit
(deviation 1.0) as on the board. `CLIFF_STRESS=1`: V5-like +-1 dB gain steps
every 3-15 ms, four 50-300 us carrier fades and two multipath phase jumps per
field.

## Findings

1. **The trained trackers are the cliff.** Under board conditions with
   stress, PAIR+AF and RANGE32 lose the whole picture in 8-25 % of cases
   even at 20-25 dB C/N (luma SINAD 6-12 dB when it is shown). The failures
   are VTXs with deviation >= ~1.05 and off-centre carriers: the
   32-phase first-order trackers slip and never reacquire. At deviation 0.69
   they stay locked and are the sharpest at weak signal.
2. **EDGE** never matches either: grainy (SINAD 1-3 dB) and loses lock at
   low deviation.
3. **Stateless span50 (HR50/HC50)** keeps lock everywhere with an ideal
   goggle AGC, but its DAC is the counter output (full +-pi over 64 codes):
   at deviation 0.69 video uses ~15 codes and noise swings reach ~+-300 IRE.
   A peak-clamp goggle then loses lock in 3-18 % of lines at moderate C/N.
   Clipping or scaling that output is not possible in the 2-bundle program
   (SPAN50.md); simulated clipping near the sync tip also hurt lock.
4. **UP1 (untrained PAIR-structured tracker, kp = 1)** - state = last
   observed phase, so nothing to lose; output through a full analytic LUT
   (nominal transfer, no fit needed). With stress, board conditions and all
   deviations: 99-100 % lock in both goggle models from 6 dB up, SINAD equal
   to or above PAIR from 8 dB, below PAIR by ~1 dB only at deviation 0.69 and
   4-8 dB. It fits the existing PAIR program (same address layout).

All-deviation stress sweep (`v2_stress_all.json`, 12 cases per C/N; mean
h_ok ideal / peak / SINAD dB):

| C/N | HC50 | PAIR+AF | RANGE32 | UP1 |
| ---: | --- | --- | --- | --- |
| 4 | 0.99 / 0.97 / -2.5 | 0.71 / 0.71 / 0.2 | 0.66 / 0.66 / -0.1 | 0.95 / 0.95 / -0.7 |
| 6 | 1.00 / 0.99 / 0.5 | 0.69 / 0.69 / 2.4 | 0.68 / 0.68 / 1.9 | 1.00 / 1.00 / 1.8 |
| 8 | 1.00 / 0.99 / 2.1 | 0.99 / 0.99 / 3.3 | 0.99 / 0.99 / 3.2 | 1.00 / 1.00 / 3.3 |
| 10 | 1.00 / 0.98 / 3.3 | 0.92 / 0.92 / 4.8 | 0.92 / 0.92 / 4.2 | 1.00 / 1.00 / 4.4 |
| 14 | 1.00 / 0.98 / 5.2 | 0.75 / 0.75 / 6.2 | 0.83 / 0.83 / 6.2 | 1.00 / 1.00 / 6.2 |
| 20 | 1.00 / 0.99 / 6.2 | 0.83 / 0.83 / 6.6 | 0.83 / 0.83 / 6.6 | 1.00 / 1.00 / 7.1 |
| 25 | 1.00 / 1.00 / 8.3 | 0.75 / 0.75 / 7.6 | 0.75 / 0.75 / 7.4 | 1.00 / 1.00 / 8.4 |

## Firmware contributors found in board logs (not yet changed)

- With every LUT demod the semantic sync measure is disabled, so the DCO
  auto-search decides "no carrier" from the envelope ratio alone (<= 1.15).
  A weak but usable carrier passes that test; after 5 s the search pauses
  AGC, jumps to another gain and steps the DC codes while video is shown
  (`autotest3.log`: repeated `DCO baseline`/`DCO_AUTO` at phase C/N 2-5 dB).
- V5 writes gain ~100 times per second near the edge (G81-G83), each with a
  DCO hold of 20-281 us. Simulated +-1 dB steps were harmless to UP1; the
  holds themselves are not modelled.
- FusionDemod program swaps halt the engine 3.8-5.1 ms (~70 lines); a goggle
  relock is not modelled but would follow every swap.

## Negative results retained

- Clipping/scaling a span50 transfer to the CVBS range: +0.3 dB SINAD, but
  sync lock worse (asymmetric clipping lifts the sync mean). Not feasible in
  the 2-bundle program anyway.
- Averaging the a- and b-chain span50 differences (`AVG`): +1-2 dB, lock
  unchanged, but needs 4 counter operations per span - infeasible.
- Quadrant of sample a in the spare decode bits (`PQ`): +0.3 dB, not worth a
  new program.
- An earlier version of the goggle model (adaptive slicer tracking candidate
  medians) produced false lock losses during fades; replaced, and all
  `v2_*` results use the corrected model. `sweep_*` files predate the H-PLL
  fix and the AGC variants; their tracker failures reproduce in `v2_*`.

## Caveats

- The earlier real-IQ click check scored UP1 kp 1.0 at 16.8 clicks vs PAIR
  6.7 on the only short board captures (pre-DCO). That must be rechecked on
  the board.
- Goggle behaviour is modelled generically; specific goggles differ.

## Reproduction

From `v4/tools/dsp_search` with the research environment:

```
OPENBLAS_NUM_THREADS=1 CLIFF_BOARD=1 CLIFF_STRESS=1 python cliff_study.py \
  --output v2_stress_all.json --per 12 --cnr 4,6,8,10,12,14,17,20,25 \
  --names HC50,PAIR_AF,RANGE32,UP1
```

## Untrained search (2026-10-11)

Operator request: the best *untrained* demod that works universally.
`untrained_search.py` builds every candidate from a closed formula (phase of
the IQ cells -> token; phase update p' = p + kp*wrap(obs - p); output = phase
advance, linear or wrapped-Gaussian posterior mean, mapped onto the 64 DAC
codes over a fixed frequency range). No LUT entry is fitted to data; the
search only chooses among 360 formula variants (layout 4411/3322, 16/32/64
phases, kp, weight of sample B, output law, range). All compile to the
existing PAIR-style program. Objective: mean over C/N of min(ideal, peak
goggle) correctly displayed lines, then luma SINAD; board conditions and
flight stress, deviation 0.6-1.4.

- Round 1 (15 cases, 364 candidates): kp 0.8 on 32 phases leads; sample B
  never helps; PAIR/RANGE32 lose whole pictures at 20 dB.
- Round 2 (84 cases, kp 0.6-0.9 refinement): kp 0.8-0.9 optimal; 0.7 worse.
- Round 3, held-out seeds (144 new cases, C/N 4-25, six deviations):

| model | picture (mean) | worst case >= 5 dB | lock at 4 dB | SINAD |
| --- | ---: | ---: | ---: | ---: |
| **U85** (4411, 32 ph, kp 0.85, linear, -4..+8 MHz) | **0.995** | **0.99** | **0.97** | 4.24 |
| U80 MMSE 0.35, nominal | 0.985 | 0.96 | 0.89 | 4.47 |
| UP1 (kp 1) | 0.977 | 0.96 | 0.84 | 3.85 |
| HC50 | 0.982 | 0.81 | 0.95 | 2.80 |
| PAIR+AF (nominal fit) | 0.828 | 0.00 | 0.75 | 4.10 |
| RANGE32 | 0.820 | 0.00 | 0.67 | 3.78 |

MMSE output combined with the narrow -4..+8 MHz range loses lock (soft
clipping near the sync tip again).

**Real-IQ click check (old captures):** U85 28.7, UP1 22.9, PAIR 12.6,
RANGE32 7.5. The weak captures are antenna-off noise with ~2.5 cells DC from
before hardware DCO. `untrained_dc_check.py`: at that DC (0.6 RMS) every demod
loses the picture (U85 0.00, UP1 0.16, PAIR 0.17, RANGE32 0.17, HC50 0.16);
at 0.3 RMS U85 keeps 1.00 while PAIR/RANGE32 keep 0.83. The click figure
therefore needs new captures with DCO on before it can veto anything.

Not established: board picture, goggle acceptance, measured range.
