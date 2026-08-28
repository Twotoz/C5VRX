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

The next physical run exposed a C5-specific ESP-IDF trap: C5 has a single
always-on entry address and does not define `SOC_PM_PAU_REGDMA_LINK_MULTI_ADDR`,
so `pau_regdma_set_extra_link_addr()` compiles to a no-op. The observed zero
current-link/address telemetry was therefore a real unprogrammed root, not an
RF timing failure. The chain now uses `pau_regdma_set_entry_link_addr()` and
its four finite WRITE descriptors live in LP SRAM. Heap descriptors are not
valid here because HP SRAM is switched to MAC-dump ownership before every
rearm. Timeout diagnostics are latched even when PAU raw/error registers stay
zero, allowing an unstarted link to remain distinguishable from a target-write
or pointer-departure failure.

The entry-link/LP-SRAM fix is now physically proven on ESP32-C5 revision 1.0.
Three bounded REGDMA windows completed 33, 42 and 60 blocks/rearms with zero
failures, followed by a direct run that reported 22,496 rearms with zero
failures. VTX removal exited HP park and restored PAL, so RF presence and loss
remain observable with the hardware backend. This proves repeated physical
REGDMA restart execution; it does not by itself prove phase-continuous IQ at
each 16K boundary.

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

The sparse WBFM BitScrambler kernel consumes four 80-MS/s IQ samples for each
CVBS byte. Its signed 10-bit I/Q unpacking and phase discriminator pass the
synthetic host validators, but that is a mathematical conversion check rather
than proof of a correctly scaled physical composite waveform.

VTX RF output is burst-gated. Consequently `words / window_wall_time` is an
activity metric (the physical run varied from about 27 to 49 MS/s), not the
sample clock represented by adjacent IQ words. The shortest completed 16K
block periods measure the active writer near the expected 80 MS/s. Direct AV
therefore preserves the RF sample timebase and clocks PARLIO at exactly
80/4 = 20 MHz. The former wall-time-derived clock reached only 6.67 MHz and
could not produce correctly timed PAL.

The existing BitScrambler -> PARLIO circular DMA path and PAL fallback remain.
Physical monitor/scope validation must still establish sync polarity, voltage
levels, RF-dependent picture content and boundary behavior before the direct
route can be described as good CVBS. Subsequent pacing work should consume only
`c5vrx_iq_stream`, use line-scale elasticity rather than a framebuffer, and
benchmark each offload.
