# C5VRX-5 findings

C5VRX-5 keeps only what the C5VRX-4 research on PR #190 (branch
`codex/c5vrx4-pair-range`) showed to help the picture. The full study, its
scripts and data live in that branch under `v4/docs/PICTURE_CLIFF_STUDY.md`,
`v4/tools/dsp_search/` and `v4/docs/data/`. This page is the summary.
Synthetic results are host simulations; board results are labelled as such.
Measured range is not established.

## The problem: the whole picture dropped out

With slightly bad video the C5VRX-4 picture did not get noisy, it vanished.
A goggle model (sync low-pass, slicer, H-PLL flywheel, ideal and peak-clamp
AGC) over full fields showed why: the trained 32-phase trackers (PAIR,
RANGE32) slip on VTXs with a larger deviation (>= ~1.05) or an off-centre
carrier and never re-acquire. They lost the whole picture in 8-25 % of cases
even at 20-25 dB C/N. The board logs added two contributors: the DC search
running under weak video, and gain hunting at the range edge.

## Where information is lost, and where clicks come from

An information ledger added one loss at a time to identical signals:

- The 4-bit IQ lanes, folding included, lose essentially nothing against
  the analog IQ. Using one sample per 50 ns span is not a loss either.
- Clicks are FM threshold physics: the noise phasor circles the origin. The
  ideal analog discriminator (an RX5808) has them too. The IQ shows when
  they happen: the sample lands in the origin cells.
- The loss is the 32-sector phase lattice (625 kHz per output step). A
  tracker with clipping recovers it; the flashed LUT reaches its own float
  bound below 14 dB C/N.

## U85Q: the demodulator

An untrained search chose among closed-form formulas only (no fitted LUT
entry), with held-out confirmation:

- state: 32 tracked phases, `p' = p + 0.85 * wrap(observation - p)`;
- observation: the PAIR4411 shared word (sample A full byte + sample B
  signs);
- origin cells: four coarse quadrant tokens with their own gain 0.5. Erasing
  them (U85E) was sharper but failed under multipath, because fades put
  real signal into the origin cells;
- output: the tracked advance, linear over -6..+9 MHz onto the DAC.

Held-out results (picture = correctly displayed lines, mean):

| model | nominal picture / SINAD | mixed robustness picture / SINAD |
| --- | --- | --- |
| **U85Q** | 0.998 / 3.55 dB | **0.893** / 1.93 dB |
| U85E (origin erasure) | 0.995 / 4.26 dB | 0.706 / 2.48 dB |
| HC50 (stateless) | 0.981 / 2.17 dB | 0.872 / 0.76 dB |

The mixed set covers echo + fading, carrier +-2 MHz, DC, I/Q 1.4/20 deg and
a CW spur. Trained PAIR/RANGE32 scored 0.83 / 0.82 on the nominal set, with
0.00 in their worst cases. Under multipath U85Q matches its float upper
bound; what remains there is in the signal, not the LUT.

The output window is the one trade-off left: narrower is sharper but clips
the sync tip when the carrier is off centre.

## Board validation (2026-10-11, real video, A1)

U85Q with the narrow filter and the DC latch, 12 snapshots:

- the carrier's sync tip sat at -5.0 MHz and white at +6.25 MHz. U85Q's
  -6..+9 MHz window holds both; U85E's -3.5..+7.5 MHz clipped the sync tip,
  so a narrower window is not safe;
- clicks per 1000 samples: U85Q 83.0, U85E 85.7, HC50 101.5.

## AFC target matched to U85Q (C5VRX-5)

C5VRX-4 centred the VTX blanking level at -436 kHz, the design centre of
the trained trackers. U85Q's window is -6..+9 MHz, and the board's real VTX
swung wider than the deviations searched (up to 1.4). Shifting the AFC
porch target, held-out simulation (board conditions with +-1 MHz carrier
error, stress, 20 cases per cell at C/N 4-20):

| porch target | picture (all) | SINAD | deviation 1.4 | deviation 1.8 |
| --- | ---: | ---: | ---: | ---: |
| -436 kHz (C5VRX-4) | 0.908 | 2.42 dB | 1.00 | 0.64 |
| **-1036 kHz (C5VRX-5: -1.0 MHz)** | **0.969** | **3.27 dB** | 0.98 | 0.90 |
| -1436 kHz | 0.961 | 3.89 dB | 0.95 | 0.91 |

The larger shift buys more SINAD but starts to drop pictures; -1.0 MHz is
the balanced choice. Board confirmation is pending.

The Direct Gain V5 band also fits U85Q: same simulation, mean over C/N
4-20 and deviation 0.69-1.4, by IQ radius (cells):

| radius | 3.0 | 3.6 | 4.2 | 5.0 | 5.7 | 6.5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| picture | 0.992 | 0.997 | 0.987 | 0.995 | 0.993 | 0.980 |
| SINAD dB | 3.16 | 3.09 | 3.32 | 3.03 | 2.72 | 1.75 |

The healthy band (P50 13..32, radius ~3.6..5.7, targets 4.1 / 5.2) sits on
the flat part; folding only costs above it. The gain controller is kept.

## The narrow analog filter

FM threshold follows the noise bandwidth before the demodulator. The board
ran ~19.4 MHz (noise bandwidth 20.7 MHz) where Carson's rule for typical
VTX video is ~13-14 MHz. Host results, U85E:

| filter | lock at 2 dB | lock at 4 dB | mean SINAD |
| --- | ---: | ---: | ---: |
| ~20 MHz | 0.66 | 0.92 | 4.26 dB |
| 14 MHz | 0.95 | 0.99 | 4.55 dB |
| 12 MHz | 0.98 | 0.99 | 4.50 dB |

14 MHz costs <= 0.3 dB on a strong signal for every tested deviation, and
helps every weak case. C5VRX-5 measures the setting per board (VTX off,
once) and keeps the one with the lowest noise bandwidth that still covers
14 MHz. (Later lowered to a 12 MHz default class, plus 10 MHz narrow and
16 MHz wide classes; `calibration.c` FILTER_MIN_KHZ.)

## Receive-chain fixes carried over

- DC: the per-gain DC DAC pair is searched only in confirmed quiet, never
  on a channel where a carrier was seen this boot, and held after every gain
  write (board: DC median ~134 LSB without the hook, ~11 with it).
- Sampling phase: about 3 boots in 10 sampled MODEM_DIAG mid-transition;
  a fourth-difference probe finds it and slips the RX clock.
- Direct Gain V5: healthy-band zero-write zone, 20 ms persistence for
  moderate excursions, burst gate and staged overload recovery.

## What did not help (kept out)

Trained LUT demods, FusionDemod/AutoFit program swaps (3.8-5.1 ms engine
halts), the sync flywheel, the level servo, adaptive lanes, the radius
boost, the vendor RX recalibration (grain on strong pictures), one lookup per
IQ40 sample, frequency-only trackers, MMSE outputs with a narrow window.

## What is left

Within this demodulator class the C5 is close to its ceiling. The remaining
dB are outside the LUT: the RF front end (LNA, filter, antenna, diversity) or
hardware with line memory at sample rate. A flight comparison against an
RX5808 is the open measurement.

## PR #196 ladder and LP handover (2026-10-12)

C5VRX by Twotoz and the C5VRX contributors. Initial operator evidence:
video appeared stable during five requested kp steps, but telemetry reached
1759 verification failures in 1760 writes. The old software shadow still
acknowledged them, so reported levels did not prove hardware contents.
Paused instruction rewriting with P produced black video while USB lived.

The first candidate (65946f1) inferred a fixed 32-bit CPU LUT port from
ESP-IDF's LL comment. Physical tests disproved it. The ESP32-C5 TRM states
that host addressing/data size must follow the configured LUT width too.
Stopped-core probes showed correct LUT16 low-half reads and neighbour bits
in some upper read halves; mode 32 accepted packed writes. Keep LUT16 and
compare only the low 16 bits. RUN writes returned unrelated RAM values and
failed almost entirely; PAUSE-only updates likewise failed and caused TX
empty events. HALT with the normal load/reset/start topology verified every
entry. The exact internal arbitration/read-latch cause is not established.
Source: [Espressif ESP32-C5 TRM, BitScrambler memory access](https://documentation.espressif.com/esp32-c5_technical_reference_manual_en.pdf).

The corrected path loads the complete target while halted, reselects each
CPU address before readback, and only publishes the shadow after all 1024
LUT16 entries verify. Failure restores baseline video. Both kp and LP
changes briefly restart output; no sample-seamless handover is claimed.
`test_kp_lut_io.c` models LUT16, address refresh, unrelated upper read bits,
a failed target and rollback, and every real kp table in both directions.
`kp_writes` counts attempted LUT16 writes.

Operator `lutfix2` telemetry reached 32,768 writes with zero verification
errors, one kp change and 31 program switches, zero transport/restart faults.
P switches measured about 5.5 ms; kp about 6.3 ms. The operator could barely
see the change. These timings belong to that build/setup, not a universal
latency or flight/range result. Continued P spam eventually hung in IDF
`parlio_tx_do_transaction` at its unbounded FIFO-ready spin (MEPC 408074e0,
control task, repeated IDLE watchdog warnings, USB heartbeat still alive).

Live TX now uses an owned nine-descriptor cycle covering the same 32-KiB
raw ring, instead of re-submitting driver loop transactions. Descriptor
ownership/zero EOF are restored with TX stopped; the FIFO-ready wait has a 20-ms timeout and
vTaskDelay so IDLE can run. A timeout returns failure and attempts baseline
recovery. The original boot/menu/RX topology and DAC pins are retained.

Every actual live change shares a five-second cooldown; failed restarts
also consume it. AUTO requires an unchanged combined kp/program target for
two seconds of usable observations in the same context. A combined target
uses one restart, not separate kp and program interruptions. P during
cooldown is refused and requires a new press later. Policy/mode reset
cannot erase the physical cooldown. `test_kp_switch_guard.c` checks boundary
times, target oscillation, coverage gaps and context changes. Diagnostics
expose switch_wait_ms, kp/prog restart times and failure counters.

Guarded owned-ring build hardware test (COM10, XIAO ESP32-C5 rev 1.0,
IDF 6.0.2, 2026-10-12): six bursts of 32 P requests, spaced 5.5 s apart,
produced six accepted program changes and 90 explicit cooldown refusals;
excess commands can be dropped by the bounded queues. Three manual kp
steps then reached verified levels 1, 2 and 3. Final totals: 9216 LUT16
writes, zero kp/prog verification failures, zero restart failures, zero
TX-empty/RX-overflow/DMA/BitScrambler faults, and no watchdog/abort. Program
restarts measured 5.94-6.19 ms, kp 5.78 ms. The observer remained alive.
This proves that tested manual sequence, not all flight conditions or the
AUTO target curve; continuous-video quality and the two-second AUTO dwell
still need field observation. Firmware was app-only flashed with a matching
partition layout and NVS/PHY left intact.

Sampling drift was observed separately and is not claimed fixed here.

## Review follow-up: recovery, calibration and output level (2026-10-12)

PR comments 6123181977 and 6123278553 identify remaining safety and
compatibility gaps. The first comment reviewed obsolete 65946f1; its
32-bit LUT-port endorsement was contradicted by the physical tests above.
The corrected LUT16/HALT access is retained.

`transport_recovery.h` drives the production restart: checked teardown,
BS enable, program load, instruction readback, complete LUT16 write/readback,
BS reset/start, RX/TX enable, RX start and bounded TX start. The last good
LUT/configuration is published only after the whole chain succeeds. Each
failed candidate is cleaned up before exactly one fully verified rollback;
failed rollback or teardown latches live OFF and reports an error without
aborting/retrying indefinitely. USB runs independently. Menu return and boot
use this same verification path. `test_transport_recovery.c` injects every
candidate and rollback stage failure, initial-load failure and failed cleanup,
and verifies publication only after successful final DMA start. These are
host coordinator tests, not physical SDK failure-injection evidence.

Manual P/k save AUTO OFF once, and AUTO is stopped/reconciled after restart
errors. Missing/invalid settings select AUTO kp+LP by the operator's explicit
decision; existing saved OFF is respected. Invalid probe/menu/scan windows
clear target confirmation. Five-second backend cooldown and two-second
continuous usable-evidence confirmation remain in force.

Issue #199 has no attached raw snapshot. A matching synthetic +1.23/-3.26
cell DC and 1.7-cell complex noise test now runs the exact shared quiet and
filter-measurement helpers, with no threshold change and no prior DC search.
100 synthetic bandwidth settings complete without a definite-carrier abort
and leave eligible quiet candidates. A too-narrow/noisy setting can be
skipped, just as in the real sweep. Weak FM (-1..3 dB model C/N) is refused
as quiet; failed/short probes, recent sync, origin and rails retain separate
refusals. These 100 synthetic settings are not the actual PHY register sweep.
The reporter's complete physical sweep and subsequent DCO remain pending.

Issue #200 manual OUTPUT is integrated via g/menu. Boot is always 1x;
1.5/2/2.5x change only DAC6 low bits. Transformation is applied to original
kp/program tables, never cumulatively to an already-scaled table. Verified
level/program/output survive menu return and rollback; T reports clipping
as the count of transformed LUT entries, not live sample clipping. HDZero
acceptance with the reporter's low-deviation VTX and a wide VTX is pending.

`research/output_level.py` uses the existing bit-exact U85Q and LP models
over PAL/NTSC, deviations .69/1.0/1.7 and C/N 6/30 dB, plus an unequal
sync/picture swing approximating issue #200 (-2.82/-1.5/+3.7 MHz): 160 model rows,
including four manual output levels and an ideal sync/white-referenced
candidate. All eight kp LUTs additionally pass identity and metadata
preservation checks. The DAC model assumes ideal 3.3 V GPIO, nominal
resistors, 200-ohm series resistor and 75-ohm termination (maximum .623 V
loaded). This differs from the issue reporter's nominal voltage model:
neither is a measured board voltage, and actual output impedance/load must
be established before interpreting compliance or selecting a voltage target.

Example, PAL strong carrier, U85Q, deviation 1.7: manual 2x clips 23.65%
of pre-LP samples and luma SINAD falls from 15.63 to 11.55 dB. The ideal
sync/white-reference candidate selects 1.19x, clips .64% and scores 15.68 dB.
At deviation .69, the nominal loaded sync depth rises from .060 V (1x) to
.127 V with the ideal 2.5x candidate, with .66% clipped samples. The high
gain cap prevents this example from reaching a nominal .286-V sync target;
gain does not increase the DAC's physical voltage span. Noise and transient
excursions can still clip even with ideal reference headroom.

The existing goggle metric fits analogue amplitude away, so its normalised
picture score cannot prove AV-input compatibility. The simulation separately
reports unnormalised nominal depth/swing/clipping. It demonstrates potential
benefit and the harm of blind 2x, not a tested live estimator, HDZero model,
actual sensitivity gain or flight-safe automatic output. Automatic OUTPUT
is consequently not enabled. A future implementation needs reliable sync,
porch **and white/burst headroom** measurements, context resets, failed-write
rollback, at least 2 s stable evidence and the shared 5 s application gap;
it must hold on invalid/weak observations rather than follow a fade.

Follow-up board evidence: firmware `5.0.0-pr.196-output2` passed all eight
kp levels in both directions including wraps, two P-spam bursts, 1x/2x menu
return, 2x preservation across kp/LP and an OFF-persistence reboot. Before
reboot: 16 kp and 3 program changes, 25,600 total verified LUT writes,
zero verification/restart/transport faults and no watchdog/abort. Kp changes
were about 4.2-4.4 ms and program changes about 3.8 ms in this VTX-off run.
After testing, AUTO kp+LP was explicitly restored and OUTPUT was 1x.

Manual F first caught a follow-up regression: full recovery teardown was
also stopping RX during the menu/status raster, so all calibrator copies
failed. The correction keeps RX and its descriptor metadata alive while
normal menu/status screens own TX; failed pipeline recovery still stops all
enabled parts. The production stop-parts helper now has a host regression
assertion for retained RX plus complete cleanup even when a stop fails.

Repeating F on the board (operator confirmed VTX OFF) then measured all
100 settings, plus calibrated baseline/recheck (102 setting log records),
and stored normal BW40 code48/skirt8, width 13.125 MHz, NBW13.280 MHz;
narrow code60/skirt0, width11.875 MHz, NBW11.899 MHz; wide code24/skirt0,
width18.125 MHz, NBW18.245 MHz, calibrated NBW20.612 MHz. It subsequently
searched 12 DC rows and measured a floor at gain72; no missing rows remained.
The bounded startup finished in 119.205 s without no_quiet_channel,
sample_failure or new transport/verification faults. Sampling phase still
reported unsettled and is an explicitly added investigation, not a passed
test. This is this board's VTX-off acceptance, not the #199 reporter's capture.

Multi-Rescue proposal test (`research/rescue_study.py`, data/rescue_study.json
and standalone plots): 64 identical-Q4 cases x 17 candidates = 1088 results,
with eight kp values for U85Q/LP and a floating adjacent-FM same-Q4 reference.
Clean cases have four seeds (two PAL/two NTSC), deviations .69/1.0/1.4/1.7,
C/N1/4/30 dB and the existing matched filter/baseline. CFO +/-750 kHz, DC1
cell and 3-ms/-30-dB fades use two seeds. Ranking uses worst-seed absolute
model line-lock >=.90, no more than .02 lock loss vs baseline, relative burst
SINAD >= baseline-1 dB and no additional clean DAC-rail exposure, plus <=1 dB
strong-signal loss. This relative burst gate does not prove decoded colour;
negative burst SINAD may still pass if the baseline is equally impaired.

Examples: .69/CN1 LP kp.50 gives +1.98 dB worst luma SINAD vs MATCHED,
worst modeled lock .960; 1.0/CN4 LP kp.70 gives +1.17 dB, lock .995;
1.4/CN4 LP kp.85 gives +1.79 dB, lock .989. No candidate meets the gates
at 1.4/1.7 CN1, the modeled 3-ms fades, or wide/DC1 at CN4. These are
conditional noise/detail improvements, not attenuation-margin measurements
or recognisable moving-video proof. Four new hardware programs are not
justified by this result. The matched-topology restriction is reconciled
since (research README section 17); no universal automatic rescue claim.

The first non-oracle OUTPUT probe (`research/output_probe.c`) runs the
production AFC/Direct Gain helpers passively. It rejects all modeled CN-10,
CN1 and two windows of a real board VTX-off snapshot. Strong narrow PAL
accepts191/196 windows; median sync bias .86 kHz, p95-p5 spread15 kHz,
candidate gain1.61 with spread.039. The conservative five-sync-span white
reserve refuses normal/wide candidates; the global C/N proxy also
underestimates wide strong-carrier confidence. This prototype therefore
does not qualify a universal automatic OUTPUT servo. White percentiles
from a few lines cannot guarantee headroom for a subsequent bright scene.

## OSD key bench, step 1 (2026-10-12, firmware build only)

Goal: a real overlay (signal, sync, debug text from the C5) on top of the
recovered video without touching the demodulator. The composite waveform
only exists inside the TX BitScrambler (U85Q uses both bundles; in LUT16
mode the counter shares the LUT address bits), so the overlay is a second,
separate 1-bit key stream mixed after the DAC: key pin -> Schottky diode ->
resistor -> DAC node. A 1 adds white; a 0 leaves the diode blocking, so the
tested DAC network is not loaded.

- Key source: I2S0 DOUT from `I2S_CLK_SRC_PLL_240M`, 40 MHz mclk (integer
  /6), 20 MHz bclk: one key bit per two DAC samples, from the same PLL as
  PARLIO TX (`PLL_F240M/6`), so key and video cannot drift.
- Owned GDMA chain as in `transport.c` (suc_eof cleared, interrupts off):
  zero runs from one shared 1 KiB buffer and one 26-word content run per UI
  scanline. The layout is taken from the menu raster itself (where the
  emitter sends each UI row), so no line formula can disagree with it.
  Content runs are shared per row type and bit phase: PAL 1131 descriptors
  and 10 buffers (~15 KiB), NTSC 908 descriptors and 83 buffers (~20 KiB),
  allocated only while armed.
- NTSC: the default menu loop (1,334,668 samples) is no whole number of
  32-bit key words. While the key is armed the NTSC menu raster uses a
  1,334,656-sample loop (-9 ppm line rate, colour carrier closed over 119437
  cycles, +~1 Hz; lines stay 2540/2544 samples, edge rounding <= 2
  samples). Disarmed, the tested raster is unchanged bit for bit.
- `transport_start_chain` starts the key and the menu/status raster in one
  interrupt-free instant; the remaining offset is a fixed hardware latency,
  set with console `< > , .` (dx, 50 ns steps) and `+ -` (dy, lines). Each
  change rebuilds both chains and restarts them (the picture blinks).
- Scope: menu/status raster only (we generate its sync). Live video never
  gets a key: that needs genlock to the VTX (step 2: sync positions from the
  existing ring snapshots, `main/afc.h`, plus a per-field re-render of the
  text for the VTX crystal offset).
- Host test `test/test_osd_key.c` assembles the key frame exactly as the
  chain emits it and checks every bit against the actual PAL and NTSC menu
  rasters: all on UI samples, never on sync, burst, blanking or the vertical
  interval; dx/dy move the frame exactly.

Wiring (XIAO ESP32-C5): key on header D10 (GPIO10). To free it, IQ bit I0
(MODEM_DIAG 14 on the ultrafine lane) moved from GPIO10 to GPIO2 (pad A1,
unconnected like the other IQ pins); the DAC pins are unchanged. The IQ
pins are GPIO-matrix routes only; a different pad may shift the read
instant by a pad delay, which the per-boot sampling-phase check covers
(board confirmation pending). Model of the 6-bit network into 75 ohm, Schottky
Vf 0.35 V: blanking (code 20) 0.33 V; with the key 470 ohm +0.19 V,
330 ohm +0.27 V, 220 ohm +0.38 V, 150 ohm +0.53 V (14 mA from the pin).
Start at 470 ohm; ~150-220 ohm for readable white; never below 120 ohm.

Not yet shown: I2S start/latency on the board, the bit order on the wire,
GDMA load next to PARLIO (`tx_empty`, `rx_overflow` in `T`), goggle lock on
the -9 ppm NTSC loop, the mixing resistor value, and goggle behaviour.
