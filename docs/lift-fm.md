# LIFT-FM — exact adjacent Phase5 lifting in the C5 budget

LIFT-FM is the exact-math research path for issue #23. It separates the problem
into two parts that must not be conflated:

1. **phase-domain exactness** — preserve the middle-sample winding and produce
   the exact two-adjacent Phase5 result;
2. **raw-Q4 scheduling** — obtain the required Phase5 information from the live
   40 MS/s Q4/I4 stream without exceeding the ESP32-C5 BitScrambler budget.

This PR solves and exhaustively proves the first part. It deliberately does not
claim that the second part is solved yet.

## The lifting identity

For Phase5 states p, m, c in Z32:

    d0 = wrap32(m - p)
    d1 = wrap32(c - m)
    pair = d0 + d1

The current endpoint discriminator sees only:

    endpoint = wrap32(c - p)

The exact relation is:

    pair = endpoint + 32*k
    k in {-1, 0, +1}

So exact adjacent FM does not need a second wrapped endpoint. It needs the
correct **lift** of the endpoint from the circle into the unwrapped local
trajectory.

That is the information Golden loses when it discards m before the branch
decision.

## Exact two-LUT decomposition once Phase5 is available

The naive truth table has 15 input bits:

    p[4:0] + m[4:0] + c[4:0]

The exhaustive decomposition in tools/lift_fm_synth.py finds an exact split:

Stage 1, 11 address bits:

    p[4:2]    3
    m[4:0]    5
    c[4:2]    3
              --
              11

Those 2048 addresses collapse to only **34 distinct future functions** over the
remaining four low endpoint bits. Therefore a 6-bit token is sufficient.

Stage 2:

    token     6
    p[1:0]    2
    c[1:0]    2
              --
              10

The result is the exact 6-bit P20/G2 DAC code for:

    wrap32(m-p) + wrap32(c-m)

with no second wrap.

The self-test evaluates all:

    32 * 32 * 32 = 32768

Phase5 triplets. One mismatch fails CI.

## Packed x8 SIMD add

There is a second exact identity intended for a future BitScrambler schedule.

Embed a Phase5 state into an 8-bit lane:

    E(x) = 8*x mod 256

Then construct:

    initial.low  = -E(p)
    initial.high = -E(m)

    addend.low   =  E(m)
    addend.high  =  E(c)

A single 16-bit addition produces both adjacent differences in parallel.

The low lane is exactly d0. A carry from the low byte can enter the high byte,
but every encoded phase is a multiple of eight. The carry therefore touches
only the three guard bits [2:0]; high bits [7:3] still contain the exact d1.

tools/lift_fm_synth.py proves this for every Phase5 triplet as well.

This removes the old need for four separate current/previous counter-add
bundles merely to obtain the two wrapped differences.

## What is still unsolved

Production does not receive Phase5 symbols. It receives:

    MODEM_DIAG raw Q4/I4 @ 40 MS/s

The exact raw-byte -> Phase5 map depends on all eight raw bits. The current
two-bundle live path has only one LUT result per BitScrambler cycle, so simply
placing the exact phase-domain two-LUT network after two raw phase lookups would
still exceed the schedule.

Therefore this PR does **not** add a misleading LIFT-FM menu option.

A live LIFT-FM mode is allowed only after a candidate schedule proves all of:

- every acquired 40 MS/s IQ sample participates before 2:1 reduction;
- raw Q4/I4 -> required state is exact, not a learned approximation;
- 32768/32768 Phase5 oracle agreement;
- no second wrap of d0+d1;
- at most two steady-state BitScrambler bundles per 50 ns output period, or a
  separately proven architecture with equivalent sustained throughput;
- state continuity across the cyclic DMA boundary;
- no PARLIO TX FIFO underrun.

## Why this is useful now

This narrows the remaining search considerably.

We no longer need to discover a compact approximation to the complete adjacent
FM waveform. The exact phase-domain transform is only a 34-state residual
problem, and the two wrapped differences can be formed by one packed add.

The remaining target is specifically:

    raw Q4 decode + LIFT state scheduling

under the physical BitScrambler constraints.

That is a much smaller superoptimization problem than the legacy
phase -> delta0 -> delta1 -> accumulator -> mapper pipeline.

## Reproduce

    python3 tools/lift_fm_synth.py --write --self-test
    git diff --exit-code -- main/lift_fm_phase_lut.h

The generated table is deterministic and dependency-free.


## LUT16 exact 10 -> 5 -> 10 hardware oracle

The first proof used an 11-bit stage-1 partition and found 34 residual
functions. A second exhaustive search specifically constrained both stages to
the ESP32-C5 16-bit LUT address width and found a tighter exact decomposition:

```text
stage 1:
    p[4:2]     3
    m[4:1]     4
    c[4:2]     3
               --
               10 bits

    -> 5-bit token
       only 24 token values are used

stage 2:
    p[1:0]     2
    m[0]       1
    c[1:0]     2
    token      5
               --
               10 bits
```

That means both stages fit the native **1024 x 16** C5 LUT mode exactly.

Even better, they can share the same physical LUT word:

```text
bits  0..5   stage-2 exact DAC code
bits  8..12  stage-1 token
```

An address collision between stage 1 and stage 2 is therefore harmless: the
two roles occupy disjoint result bits.

`main/fm_lift16_phase.bsasm` is a real BitScrambler program implementing this
phase-domain oracle. After priming, the hardware loop is exactly:

```text
emit
  -> writes exact [D,D]
  -> addresses stage 1 for the next pair
  -> preserves the five free bits

lift
  -> consumes the stage-1 token
  -> addresses stage 2
  -> reconstructs next endpoint state
  -> jump emit
```

So steady-state cost is exactly **two bundles per 50 ns output**.

The subtle state trick is important. `emit` needs all `O0..O15` for the
duplicated DAC word, so it stores the five stage-2 free bits in `O26..O30`.
During `lift`, the next endpoint `p=c` is reconstructed into `O0..O4`
from the saved low c bits plus the c high bits still present in the previous
stage-1 address. No third state-maintenance bundle is needed.

### Boundary that still remains

The LUT16 oracle consumes **predecoded Phase5 symbols**. It proves that once
`p,m,c` exist, the exact adjacent transform and P20/G2 map fit in the real
two-bundle C5 schedule.

The flight stream is still raw Q4/I4. Two fresh raw symbols arrive per output,
and exact raw-byte -> Phase5 decode is itself nonlinear. Therefore
`fm_lift16_phase.bsasm` is intentionally compiled but not exposed as a flight
demodulator.

The remaining superoptimization target is now narrower:

```text
two raw Q4 bytes / 50 ns
    -> exact information equivalent to the LUT16 oracle inputs
    -> without adding a third lookup/bundle
```

Reproduce the LUT16 proof with:

```sh
python3 tools/lift_fm_lut16.py --write --self-test
git diff --exit-code -- main/fm_lift16_phase.bsasm
```
