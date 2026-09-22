# Exact-adjacent M2M demodulator

This document describes the experimental `ADJ M2M` demodulator introduced
for Issue #23.

## Goal

This experiment does **not** increase the RF sensitivity of the ESP32-C5
frontend. Its purpose is to move the current demodulation cliff farther into a
weak/noisy signal by retaining phase-trajectory information that Golden throws
away.

Golden effectively decides one 50 ns endpoint interval after one acquired
40-MS/s Q4/I4 sample has been skipped:

```text
p -------- m -------- c
|                     |
+---- endpoint FM ----+
```

At large legitimate FM deviation or weak-IQ phase ambiguity this can choose the
wrong circular branch. A representative case is:

```text
p -> m ~= +140 deg
m -> c ~= +140 deg

adjacent trajectory ~= +280 deg
endpoint p -> c      ~= -80 deg after wrap
```

The repository's frozen-capture analysis measured the same class of failure at
8.351% of endpoint intervals overall and only 0.285% on the strong-IQ subset.
That makes demodulation, not only RF gain, a plausible usable-range gate.

## Live architecture

```text
MODEM_DIAG Q4/I4 @ 40 MS/s
        |
        v
32 KiB raw cyclic PARLIO RX ring
        |
        | completed 16 KiB half, synchronous zero-copy
        v
BitScrambler M2M
        |
        | Phase5 estimate of EVERY 40M Q4/I4 sample
        | d0 = wrap5(phi[m]-phi[p])
        | d1 = wrap5(phi[c]-phi[m])
        | pair = d0 + d1       <-- never wrap pair again
        |
        | Q4-cell angular confidence
        | low-confidence winding only -> one-sample coarse hold
        v
20 MS/s CVBS information
        |
        | duplicate every final code
        v
[D,D] bytes @ 40 MHz
        |
        v
triple-buffered 48 KiB output ring
        |
        v
plain PARLIO TX -> 6-bit resistor DAC
```

Raw 25 ns discriminator values are never sent directly to the DAC. The final
video information rate remains 20 MS/s, retaining the quieter 2:1 combine
behavior of the production path.

## Why exact adjacent Phase5

The full-Q4 phase8 adjacent implementation remains the offline upper-bound
oracle, but its straightforward live kernel exceeds the ESP32-C5 BitScrambler
instruction-memory/rate budget. C5 hardware accepts at most eight instruction
bundles in one program.

The live experiment therefore keeps the already-proven Phase5 quantizer but
applies it to **every** acquired Q4/I4 sample instead of discarding the middle
sample first. The important experimental variable is the measured root cause:
trajectory/winding retention.

One Phase5 turn is 32 states:

```text
adjacent delta = -16 .. +15
two-delta sum  = -32 .. +30
```

Each adjacent delta is wrapped individually. Both are sign-extended into
Counter A and added. The pair is not reduced modulo 32 again.

At Phase5 resolution the user's illustrative +140/+140 degree case is close to
`+12 + +12 = +24` states. Endpoint-only wraps `+24` to `-8`; ADJ M2M
keeps `+24` for the final 2:1 CVBS mapping.

`tools/test_adjacent_math.c` locks this behavior in CI.

## Eight-bundle kernel

The 1024x16 LUT is multi-role, with independent bit fields:

```text
bits  0..4   signed delta5 for a Phase5 pair lookup
bits  5..9   final CVBS / 2
bits 10..14  Phase5 for a raw Q4/I4 lookup
bit      15  raw Q4-cell low-confidence flag
```

The steady pair loop is exactly eight instructions:

```text
1 address middle raw sample
2 save middle + address current raw sample
3 save current + address d0
4 load signed d0
5 address d1
6 add signed d1
7 address final map using pair + confidence + previous output
8 emit [D,D] and retain current phase/output state
```

This is deterministic adjacent Phase5, not a trained trajectory estimator.

## Confidence and phase-slip protection

Q4/I4 contains only 256 Cartesian symbols. For each symbol the LUT generator
uses the actual truncated Q10 cell:

```text
signed nibble s -> [64*s, 64*s+63]
```

The transformed cell corners estimate angular uncertainty. Cells that include
the origin, or have a sufficiently broad angular spread, are marked low
confidence.

A sample is **not** repaired merely because its FM motion is large. A hold is
allowed only when both are true:

1. the middle Q4 cell is low confidence;
2. the two-adjacent pair sum proves a full winding that endpoint Phase5 would
   fold (`pair < -16 || pair >= 16`).

The recovery is deliberately small: one output sample coasts from a coarse
previous-CVBS bucket. This is a bounded PLL-lite/holdover primitive, not a
general smoothing filter. Strong, credible large FM motion passes unchanged.

The compact final LUT stores `CVBS/2`, so this experiment uses even DAC codes.
The linear Phase5 mapping and hold buckets are naturally even; saturated white
is deliberately 62 instead of 63.

## Realtime scheduling

At 40 MB/s a 16 KiB raw half-ring spans:

```text
16384 / 40,000,000 = 409.6 us
```

The completed half is fed synchronously and **zero-copy** to M2M while PARLIO
RX writes the opposite half. The writer-half is checked before and after every
transform. If RF reaches the input half again before M2M completes, the result
is ambiguous and is rejected.

The output has three 16 KiB slots:

```text
TX slot 0 -> TX slot 1 -> TX slot 2 -> repeat
```

That avoids the approximately 2x transform-speed headroom a two-slot ping-pong
would require.

Every hardware transform records its time. Initial experimental gates are:

- warning above 330 us;
- fail closed during startup at or above 400 us;
- startup failure restores Golden + 6BIT@40 and reboots.

Diagnostics expose transform count, last/max time, failures, short writes,
boundary holds, deadline warnings and sequence misses.

## Finite M2M boundary state

ESP-IDF's public `bitscrambler_loopback_run()` resets the BitScrambler for
every finite run. Therefore previous phase and holdover state cannot simply be
assumed to continue between 16 KiB halves.

The live path preserves the true final raw sample and final CVBS code from the
previous block. It repairs the first few output pairs of the next block with
the independent software oracle until ordinary temporal state is
re-established. This prevents finite M2M priming from silently becoming a new
ring-boundary artifact.

## Mode ownership

`ADJ M2M` is reboot-scoped because loopback claims both BitScrambler
directions on ESP32-C5. It cannot coexist with the production PARLIO-TX
BitScrambler decorator.

- Golden remains the default.
- Selecting ADJ M2M on the VIDEO page forces 6BIT@40 and reboots on exit.
- ADJ M2M uses plain PARLIO TX; M2M has already generated `[D,D]` bytes.
- 4BIT@80 always returns the demodulator to Golden.
- Holding BOOT for three seconds while ADJ M2M is active restores
  Golden + 6BIT@40 + ARC and reboots.

## Hardware A/B

Use the same VTX, camera, channel, antenna, ARC/RF configuration and physical
route/attenuation.

Compare:

```text
ARC + GOLDEN + 6BIT@40
ARC + ADJ M2M + 6BIT@40
```

At the weak-signal edge record:

- usable-picture distance/attenuation;
- endpoint winding and strong-winding metrics;
- semantic sync quality;
- visible hard specks / line shifts / decoder relocks;
- M2M last/max transform time;
- M2M deadline/sequence misses;
- PARLIO/GDMA transport faults.

A successful result is not merely a stronger-looking noisy picture. The target
is that the same weak RF input remains semantically locked longer and degrades
more gradually, with no transport faults and no loss of strong-signal detail.
