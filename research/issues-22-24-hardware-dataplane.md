# Issues 22/24: hardware IQ and CVBS dataplane

This branch starts at the exact PR21 head. PR21's LP-core, 16,384-word rearm
backend remains the only active RF producer. The rejected native-ring
experiment is not copied into this branch.

## REGDMA evidence and gate

ESP-IDF v6.0.2 for ESP32-C5 exposes PAU REGDMA WAIT and masked WRITE link
nodes, four ETM start tasks, link-loop/access/wait timeouts, and a software
start. This makes a hardware restart sequencer plausible. It does **not** prove
that an unbounded PAU link is safe during normal Wi-Fi operation.

The only allowed target for a future probe is the already audited RF dump
control register at `0x600a9008`, using only recovered bits 31 (enable), 19
(software start) and 18 (done observation). Before constructing a REGDMA link,
hardware must establish which bounded restart sequence works:

1. pulse START while ENABLE remains asserted;
2. clear DONE by a vendor-supported operation, then pulse START;
3. toggle ENABLE and pulse START (the PR21 reference sequence).

There is currently no C5 evidence for a separate DONE-clear write, so step 2
must remain unavailable rather than guessing write-one-to-clear semantics.
`REGDMA_IQ_STATUS` therefore reports capability but remains fail-closed.

## Producer-neutral stream

`c5vrx_iq_stream` exposes monotonic producer/consumer positions, physical
pointers, lead, backend, boundary gap and proof flags. Higher layers can move
to this API without learning the 16K lifecycle. Its current classification is
`LP_AUTOREARM`; `hardware_circular` and `phase_continuous` stay false.

The eventual accepted backends are deliberately named:

- `LP_AUTOREARM`: current PR21 reference/fallback;
- `HW_AUTOREARM`: REGDMA/ETM sequence, only after physical gap/phase proof;
- `NATIVE_GAPLESS`: reserved for a genuinely self-wrapping writer (currently
  rejected by physical fixed-address memory evidence).

## Issue 24 staging

The existing BitScrambler -> PARLIO circular DMA path and PAL fallback remain
unchanged. Subsequent hardware work should consume only `c5vrx_iq_stream`, add
line-scale elasticity rather than a framebuffer, and benchmark each offload.
No experimental producer may replace PAL fallback or park the HP core until RF
content, boundary phase, USB control and fault recovery all pass on hardware.
