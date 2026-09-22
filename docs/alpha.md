# Alpha predictive adjacent demod

Alpha is the next C5VRX weak-signal experiment. It is stacked on the exact
adjacent Phase5 M2M path from PR #53, but it changes the final decision from a
memoryless discriminator into a causal predictive tracker.

Alpha is not a range claim. It is an implementation intended to test whether
temporal state can move the usable-video threshold after exact adjacent has
already recovered the middle 40-MS/s sample.

## Signal path

```text
ARC RF frontend
 -> Q4/I4 @ 40 MS/s
 -> Phase5 for every acquired sample
 -> exact adjacent d0 and d1
 -> exact pair = signed(d0) + signed(d1), NO second wrap
 -> Q4 confidence for both new samples
 -> Alpha predictive 8-state tracker
 -> 20 MS/s unique CVBS
 -> [D,D] @ 40 MHz
 -> resistor DAC
```

Alpha deliberately predicts before emission. A low-confidence observation is
compared with the previous accepted output state and corrected before that
sample reaches CVBS.

## What is predictive

The previous accepted six-bit output is quantized into eight states:

```text
state = previous_code >> 3
prediction = state * 8 + 4
```

For each new exact-adjacent pair:

- high-confidence Q4: emit exact adjacent unchanged;
- low confidence + small innovation: follow the observation;
- low confidence + medium innovation: move halfway toward it;
- low confidence + catastrophic innovation: limit the step to 24 DAC codes.

The final tracker is therefore causal and stateful, but it is intentionally not
described as a full floating-point PLL. A true second-order PLL remains an
offline oracle because 40 MS/s leaves only six 240-MHz CPU cycles per IQ sample.

## Why the tracker runs after the two adjacent deltas

A per-25-ns phase-predict/update loop plus two raw phase lookups cannot fit in
the ESP32-C5 BitScrambler's eight instruction slots. Alpha instead preserves
the important ordering:

```text
use every 40M sample
 -> recover exact adjacent trajectory
 -> combine without re-wrap
 -> predictive correction
 -> emit
```

The correction still happens before CVBS output, rather than detecting a click
after it has already been transmitted.

## Eight-bundle kernel

`main/fm_alpha.bsasm` uses exactly eight bundles:

1. prime raw0 and predictor state;
2. adjacent delta 0 lookup;
3. preserve d0 and fetch raw1;
4. adjacent delta 1 lookup;
5. load d0/confidence into Counter A;
6. add d1/confidence;
7. Alpha tracker lookup;
8. emit `[D,D]` and prefetch the next raw0.

The biased deltas are each `signed_delta + 16`. Their sum is 0..62, so the
six-bit Counter A result retains winding without a second circular wrap.

## One shared 1024x16 LUT

The runtime LUT carries three views:

```text
bits 0..4    raw Q4 -> Phase5
bits 5..9    phase pair -> biased adjacent delta
bits 10..15  Alpha tracker -> corrected CVBS
```

Raw confidence is needed while the same LUT is addressed with a Q4 byte. On
raw addresses 0..255, tracker-code bit 0 is constrained to equal that
confidence bit. This changes only low-confidence tracker entries by one DAC
code at most and never changes the eight-state predictor bin.

Confidence currently requires Q4 power >=16 and rejects the four both-axis
rail corners. It is intentionally simple and deterministic for the first
hardware A/B. A later calibrated Q4-cell confidence map can replace it without
changing the live kernel.

## M2M boundary state

Like ADJ PHASE5, Alpha uses finite 16 KiB M2M jobs. The BitScrambler resets at
each job, so phase and tracker state cannot magically cross that boundary.

Alpha carries the accepted state in software and repairs only the bounded block
prefix until the reset hardware state converges. After pair zero both paths
have the same current phase. Once their three-bit predictor states match, all
remaining hardware outputs are identical to the persistent reference.

The repair cap is 512 pairs. Diagnostics expose total/max repaired pairs,
convergence misses and bounded state handoffs. If a deep fade provides no
converging pair in that window, Alpha does not stop video: the final repaired
sample is bridged to the nearest code in the hardware predictor bin and the
hardware path continues. This is boundary stitching, not CPU processing of the
40-MS/s stream.

## Host regression

```sh
python3 tools/alpha_demod_model.py --self-test
```

The model exhaustively checks LUT packing and high-confidence transparency,
then runs deterministic clean and weak FM cases. The synthetic weak case must
reduce the >=16 and >=32 DAC-code error tails versus memoryless adjacent. It is
a regression oracle, not a measured RF sensitivity result.

## Hardware A/B

Keep these fixed:

```text
RX PROFILE  ARC
BW          BW40
AFC         OFF
DAC         6BIT@40
```

Compare:

```text
GOLDEN
ADJ PHASE5
ALPHA
TRAJ V2
```

For Alpha record:

- M2M total and kernel time;
- deadline/boundary misses;
- boundary repair max;
- state convergence misses;
- semantic sync quality;
- visible hard specks/tears and decoder relocks;
- usable-picture threshold under controlled attenuation.

Only a repeatable reduction in required input power at matched picture quality
counts as a range improvement.
