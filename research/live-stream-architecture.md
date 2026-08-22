# Prepared live RF streaming architecture

> Investigation update: the source-level non-blocking producer and guarded
> ring source are reconstructed and implemented. Physical continuous/wrap and
> phase-bearing behavior is not yet proven, so the source remains explicitly
> `EXPERIMENTAL_RING_SOURCE_UNPROVEN`.

## Status

**IMPLEMENTED / NOT PHYSICALLY TESTED**

- `c5vrx_rf_source_t` block ownership/acquire/release interface;
- bounded four-block producer/consumer queue and high-water accounting;
- separate source and processing tasks with drop/backpressure counters;
- monotonic stream accounting in the ring source: `producer_absolute` /
  `consumer_absolute` extend the wrapped hardware pointer into an infinite
  logical stream; lag current/max are part of `C5VRX_LIVE_RING_STATS`
  (issue #5 section 1 bookkeeping);
- canonical video timing observer (`c5vrx_video_timing`, host-tested):
  standards-correct vertical-interval state machine (equalizing/serration
  pulses, half-line grid phase), PAL/NTSC classification from field
  duration over the measured line period with hysteresis, field parity
  validated by the alternating interlace signature (slips counted, never
  inferred from brightness), and composite/FM polarity learned through
  configurable votes then held across ordinary blocks - opposite-polarity
  sync candidates are rejected and counted (PR #9 lesson folded in,
  issue comment 2);
- structure-derived composite level recovery (`c5vrx_cvbs_levels`,
  host-tested): minimum-envelope sync tip / back-porch estimators that
  burst energy cannot lift, a multi-second-release peak-white envelope,
  and slew-limited bias/gain recommendations feeding the conditioner's
  new external-bias pivot mode (issue #5 section 5);
- residual fractional clock bridge (`c5vrx_clock_bridge`, host-tested):
  Q32 fixed-point rate adapter with bounded elastic FIFO, linear or
  Catmull-Rom cubic interpolation (cubic measurably preserves more
  chroma amplitude at mismatched ratios), occupancy telemetry and counted
  drops (issue #5 section 4);
- PAL/NTSC color decoder core (`c5vrx_chroma`, host-tested): continuous-NCO
  burst PLL with evidence-based V-axis switch tracking, quadrature U/V
  demodulation over a phase-neutral centered moving-average reference,
  1H line-comb Y/C separation with luma-referenced fallback, clean color
  killer without burst lock (issue #5 section 8 core);
- persistent sample-domain route through the existing 4:1 C5 BitScrambler WBFM
  transform (no CPU `atan2` in the live path);
- configurable DC tracking, bias, polarity, Q8 gain, optional one-pole filter,
  and output clamp;
- two-buffer 20 MS/s PARLIO/GDMA live sample sink;
- USB preview decoupled from the AV hot path: the pipeline sink calls
  `c5vrx_cvbs_live_out_write()` first and then hands the same samples to a
  bounded 16 KiB SPSC staging ring (`c5vrx_usb_preview_submit`); the priority-4
  preview worker drains that ring and owns all sync tracking/frame assembly.
  The staging ring drops newest samples when full and counts them
  (`staged_drop_samples`, `staged_peak_bytes` in `C5VRX_CVBS_LOCK`), so USB can
  add latency to itself but never to RF/DSP/PARLIO. `c5vrx_usb_preview_ingest`
  remains only for synthetic `BENCH USB_PREVIEW`;
- finite vendor dump adapter and `NEARLIVE START` diagnostic;
- source/output underruns, dropped blocks, discontinuities, stage timing,
  achieved rate, WBFM/CVBS ranges, occupancy and high-water diagnostics.

**UNKNOWN UNTIL MEASURED ON HARDWARE**

- whether finite chained dumps contain useful analog-FPV I/Q;
- actual sample rate and gaps;
- RF-to-CVBS polarity, bias, gain, filtering and voltage calibration;
- sustained PARLIO timing and passive DAC levels into the real 75-ohm input.

**CONTINUOUS RF PRODUCER UNKNOWN**

An `EXPERIMENTAL_RING_SOURCE_UNPROVEN` module is now implemented. It arms the
non-blocking producer once, keeps a guarded reader pointer, exposes only
contiguous segments, immediately copies into owned DMA buffers to uphold the
immutable-block contract, and counts wraps, overruns, missed words, drops and
discontinuities. It cannot be relabeled `CONTINUOUS` until cadence, wrap and
coherent phase-boundary probes pass on hardware.

Promotion to `kind = C5VRX_RF_SOURCE_CONTINUOUS` is the remaining gate. The
experimental ring source already implements `acquire`/`release`; hardware must
show that its blocks are phase-bearing and gap-free enough for that stronger
classification. All downstream buffering, hardware WBFM, conditioning, output
and diagnostics remain unchanged.

Key unresolved question:

> Can the ESP32-C5 provide a sufficiently continuous, phase-bearing RF stream
> for real-time WBFM demodulation?

`RF DEEP PROBE` is now the first-board gate. The optional non-blocking producer
is hash-pinned, disabled by default and limited to vendor-derived modes 0/11.
Vendor mode 12 is deliberately rejected because its complete branch starts
BLE RX.
It is diagnostic infrastructure, not a promoted continuous source.

`NEARLIVE START` repeatedly uses the recovered finite vendor dump. Every block
is flagged discontinuous and logs identify the mode as
`FINITE_CHAINED_NOT_CONTINUOUS`. It is an experiment, never evidence of a true
continuous producer.

The route deliberately has no full-frame buffer:

```text
RF source -> 4-block queue -> persistent BitScrambler 4:1 -> conditioner
          -> fixed-size sample chunker -> two PARLIO blocks -> 6-bit resistor DAC
```

Contiguous ring segments may be shorter at wrap. The chunker carries only one
PARLIO block of samples and joins those variable segments without creating a
framebuffer or inserting a timing gap. One CPU phase-LUT calculation repairs
the BitScrambler program's first/priming delta at each continuous block
boundary; an actual source discontinuity deliberately resets it to bias.

Production `LIVE START` is bound to that route, but remains fail-closed until
the current boot has an unambiguous mode-0 cadence, coherent wrap validation,
30-second staged soak, externally measured/confirmed alias-safe bandwidth,
BitScrambler self-test, PARLIO benchmark and at least 20% synthetic pipeline
margin. A retune invalidates the RF-dependent gates. The current program has no
IQ anti-alias filter, so only a measured FE-bandlimited /4 input may pass.

Any ambiguous service interval, reader/guard collision, copy-time overwrite,
unexpected producer stop or PARLIO error stops the streaming tasks fail-closed.
RF/output ownership remains locked until `LIVE STOP` performs teardown and
prints the exact ring failure reason; probes cannot race that state.

Calibration defaults are placeholders. Change the Kconfig Q8 bias/gain,
polarity, filter shift and clamps only from measurements of a controlled VTX.
