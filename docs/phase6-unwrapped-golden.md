# PHASE6 — Unwrapped Golden

PHASE6 keeps the proven Golden output cadence but removes Golden's 50 ns
phase-wrap ambiguity.

## Problem

Golden observes only the two 50 ns endpoints. A real +220 degree phase motion
therefore aliases to -140 degrees because both endpoints are identical modulo
360 degrees.

## Architecture

```text
MODEM_DIAG Q4/I4 @ 40 MS/s
  -> RX BitScrambler: exact raw8 -> Phase5 every 25 ns
  -> 32 KiB Phase5 ring @ 40 MB/s
  -> TX BitScrambler: preserve the middle-sample winding
  -> signed 6-bit 50 ns phase motion
  -> P20/G2 CVBS mapping
  -> [D,D] @ 40 MHz DAC (20 MS/s unique)
```

The phase-domain identity is:

```text
d0 = wrap32(m - p)
d1 = wrap32(c - m)

phase6_delta = d0 + d1
```

The sum is deliberately **not wrapped a second time**. Its exact range is
[-32, +30], which fits signed six-bit state. This is equivalent to maintaining
a locally unwrapped Phase6 trajectory and evaluating U[c] - U[p].

Example:

```text
p =   0 deg
m = 110 deg
c = 220 deg

Golden:  wrap(c-p) = -140 deg
PHASE6:  +110 + +110 = +220 deg
```

PHASE6 therefore uses all 40 MS/s phase observations for unwrap continuity
while retaining Golden's 50 ns discriminator cadence and duplicated [D,D]
output. It is not a 40 MS/s adjacent-video-output mode.

## Hardware topology

The ESP32-C5 has independent RX and TX BitScrambler channels. PHASE6 uses:

- RX channel: one-byte raw Q4/I4 -> Phase5 conversion at 40 MS/s.
- TX channel: two-bundle 50 ns Phase6 backend.
- The RX preprocessor uses `prefetch=false` and explicit self-priming. This
  avoids the C5 RX `in_idle` reset timeout and the earlier empty-prefetch
  black-screen failure.
- PHASE6 is boot-only because its DMA ring stores Phase5+metadata instead of
  raw Q4/I4.

## Test

From Golden, send `J` on the serial console. The firmware persists PHASE6 and
reboots. Send `J` again to persist Golden and reboot back.

At PHASE6 boot, inspect:

```text
PHASE6_HW_PROBE ...
```

A varied `phase_mask` with many transitions proves the RX Phase5 stream is
alive. A constant mask means the RX-BitScrambler/PARLIO path still needs work.

This remains an experimental hardware mode until live CVBS and weak-signal A/B
testing beat or match Golden.
