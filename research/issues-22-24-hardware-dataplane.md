# Issues 22/24: hardware IQ and CVBS dataplane

This branch starts at the exact PR21 head. PR21's LP-core, 16,384-word rearm
backend remains the proven fallback RF producer. This branch adds an
experimental LP-triggered REGDMA rearm backend ahead of it; the rejected native-ring
experiment is not copied into this branch.

## REGDMA evidence and gate

ESP-IDF v6.0.2 for ESP32-C5 exposes PAU REGDMA WAIT and masked WRITE link
nodes, four ETM start tasks, link-loop/access/wait timeouts, and a software
start. This makes a hardware restart sequencer plausible. It does **not** prove
that an unbounded PAU link is safe during normal Wi-Fi operation.

The only allowed target for a future probe is the already audited RF dump
control register at `0x600a9004`, using only recovered bits 31 (enable), 19
(software start) and 18 (done observation). Adjacent `0x600a9008` is the
physical pointer/mode readback. Before constructing a REGDMA link,
hardware must establish which bounded restart sequence works:

1. pulse START while ENABLE remains asserted;
2. clear DONE by a vendor-supported operation, then pulse START;
3. toggle ENABLE and pulse START (the PR21 reference sequence).

There is currently no C5 evidence for a separate DONE-clear write, so step 2
must remain unavailable rather than guessing write-one-to-clear semantics.
`REGDMA_IQ_STATUS` reports both capability and live PAU flow-error telemetry.

## Experimental LP-triggered REGDMA rearm chain

The first physical attempt used PAU WAIT plus `REGDMA_EVT_DONE3` feedback. It
failed with PAU `flow_err=6` and only partial first-generation writes. That
event describes PAU-chain completion, not an RF-writer DONE source, and the
initial WAIT can expire before RF DONE. The revised experiment removes both
WAIT and ETM feedback. LP observes the already-proven RF
`DONE && PTR==16383` boundary and emits one PAU link-3 start. REGDMA then
performs masked ENABLE low/high and START high/low. LP verifies pointer
departure and falls closed on PAU error or timeout. This offloads the four
timing-critical modem writes without inventing an RF-DONE ETM source.
Because REGDMA is an independent APM bus master on C5, both LP_CORE and
REGDMA are explicitly assigned to REE0; the narrow REE0 peripheral grant
then covers the audited MODEM target as well as LP's PAU control access.
Any PAU completion error or pointer-restart failure automatically clears the
experimental request, so the next scan uses the proven LP autorearm backend;
the failing PAU registers remain latched in `REGDMA IQ STATUS`.
LP autorearm is also the boot default: REGDMA is selected only by the explicit
`REGDMA IQ ENABLE` diagnostic command and can be deselected without reflashing.

This is deliberately labelled `REGDMA_ETM_EXPERIMENTAL`. It is not promoted to
`HW_AUTOREARM` until hardware shows repeated real generations, zero PAU flow
errors, RF-dependent contents and measured boundary/phase behavior.

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
