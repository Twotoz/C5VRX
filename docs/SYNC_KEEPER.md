# Keep real snow visible while the display remains synchronized

C5VRX by Twotoz and the C5VRX contributors. This extends the archived
`legacy/c5vrx4/docs/range-max.md` sync flywheel, `research/sim.py` regeneration
models and the root U85Q-LP hardware path; it is not independent rediscovery.

## Actual requirement and current status

The operator wants **real received video/snow**, including barely visible
image fragments, with continuously valid PAL/NTSC synchronization. Random
generated snow, a black NO SIGNAL raster and a frozen frame all hide current
camera information. A pixel overlay does not solve synchronization.

The monitor's own No Signal message appears after gradual RF degradation,
while booting with VTX off can initially display noise. This is consistent
with decoder acquisition/tracking/muting behavior, but does not prove it.
It does not establish that IQ disappeared. At the onset, distinguish:

| Layer | Needed evidence |
| --- | --- |
| RX/IQ actually becomes flat | `I` capture, gain/DC tuple, histogram/variance/repeats |
| TX/BS stops or output becomes flat | transport counters, verified program, loaded CVBS waveform |
| Firmware replaces camera content | `VIDEO_OUTPUT source`, fallback state |
| Display mutes noisy CVBS | actual CVBS remains noisy on scope/CRT while LCD says No Signal |

The supplied `c5vrx-2026-10-13T16-41-22-487Z.log` is startup, not a captured
range-loss event. PR #212 is identified; the mutable PR version alone does
not identify an exact commit. It records repeated IDLE watchdog warnings
with gain_obs running, 97427 gain observations by uptime 38 s, startup still
active, no fast-watch samples, and later a progressing filter calibration.
Control copy has zero failures, max 25 us, LP active at high gain. These
facts cannot demonstrate display recovery or an RF range gain.

**Changed default:** black fallback is OFF at every boot, preserving real
live snow. Console `N` opts into the existing black-raster bench mode;
`B` retains the live-only restart diagnostic. The sync keeper described below
is research, not enabled streaming firmware or an affected-LCD cure.

## Resolve CPU starvation first

The 200-us timer previously notified gain_obs every tick. When a full
envelope measurement took longer than that, a notification was already
pending at its next wait: it remained runnable, starving lower-priority
IDLE and calibration. Seeing gain_obs in watchdog registers is not proof of
an infinite loop; the notification/service-rate mismatch is sufficient.

The 200-us gain/sentinel cadence is retained. Most gain windows now omit
unused envelope arithmetic; continuously runnable gain batches are bounded
by an 800-us wall-time budget followed by a one-tick block, instead of
taskYIELD (which cannot let IDLE run). Full envelope C/N runs at 20-ms cadence and immediately on
gain/PHY/channel/table context change. Intermediate windows retain identical
gain statistics using `dg_measure_gain`. C/N publication uses a sequence
guard and expires after 40 ms; old-context evidence is UNKNOWN. Watchdog
configuration is unchanged. A tested uniform 1-ms observation alternative
increased modeled -22 dB step recovery from 20.4 to 52 ms and failed the
existing recovery limit, so it is not selected. This addresses the diagnosed starvation path,
but actual CPU headroom and absence of new warnings need the next board log.

C/N now also refuses measurements during Direct Gain's measured settling state
and for at least 500 us after a gain write. Otherwise a transient envelope
could be cached as poor carrier C/N for the following 20 ms. The same rule
applies at cache publication and consumption; unknown/paused/busy evidence is
not used to justify a correction. Host boundaries and the firmware build pass.

## The required output behavior

Learn line period, phase, field phase and PAL/NTSC parity from trustworthy
camera sync while the signal is usable. When pulses become unreliable, coast
that timebase instead of allowing the LCD's clock to lose its reference.
Regenerate horizontal tips, porches and complete interlaced vertical pulse
trains at the real load's proper levels. Do not replace active-picture
samples with generated snow. Genuine snow will not become a clean picture,
but residual information must remain visible. Colour may need to drop to
monochrome when burst phase is unreliable; sync retention is separate from
colour lock.

Return to reliable camera timing by bounded phase/frequency correction,
avoiding an abrupt shortened line, incorrect half-line parity or a restart
on every sync decision. Normalized sync levels matter too: correct timing
with insufficient amplitude can still fail the LCD. A free-running line
counter alone is not a PAL/NTSC composite signal; vertical timing, parity,
equalizing/broad pulses and decoder behavior also matter.

## Candidate firmware route: mix IQ sources before BitScrambler

The live TX currently demodulates Q4/I4 directly from the RX ring. There is
no decoded-video RAM buffer. A candidate DMA chain could feed:

- Synthetic **IQ** segments during sync/blanking; their phase rotation makes
  the existing U85Q-LP demodulator produce known sync/porch/burst DAC codes.
- Unmodified, fresh **actual RX-ring IQ** during active video.

Keep the same 40-MS/s byte stream, two bundles per 50-ns input pair, LUT16,
continuous LP state and 40-MHz repeated DAC output. The CPU prepares metadata
and tracks clocks; it must not compute or pace every decoded video sample.
This avoids capturing/freezing an entire field and avoids adding a third
BitScrambler bundle to its already occupied tracker/LP schedule.

This is a candidate, not a demonstrated safe DMA topology. Open problems:

1. **Source age:** every real segment must be read after RX completed it and
   before RX overwrites it, including wraps, late descriptor updates and
   DMA prefetch. A replay of a static ring is not proof of live information.
2. **Geometry:** a full-frame descriptor cycle does not align with the 32-KiB
   RX ring. PAL needs 64 frames before both wrap together; NTSC needs 8192.
   Even one 12-byte descriptor per full line needs 480000 / 51609600 bytes.
   A single frame reused unchanged will drift into stale/wrong IQ. Candidate
   alternatives are verified rolling descriptors, or a ring sized to an
   exact PAL/NTSC line-group duration, with separately scheduled vertical
   intervals. They require prototype timing/ownership proofs.
3. **Camera phase:** a generator at nominal PAL/NTSC timing will drift against
   the camera. Track precise source sample indices across ring wraps and
   field parity; coarse descriptor positions alone are insufficient. Clock
   error during holdover can move the generated blanking into actual picture.
4. **Tracker transitions:** synthetic IQ changes phase history. Leave real
   porch time for convergence before active video and verify the LUT/LP state
   across every source boundary. Synthetic frequency is not automatically
   equivalent to exact requested DAC codes at a transition.
5. **Output levels:** a stable tip does not suffice if random active output
   crosses sync thresholds or sync is too shallow into 75 ohms. Output gain,
   clipping, pedestal and burst behavior must be measured.
6. **Continuous operation:** changes to the live descriptor chain, RX geometry
   or tracker need bounded rollback and hardware throughput/FIFO validation.
   No unbounded retries, ring modification races or repeated restarts.

An output-side hardware sync/blanking mux is a separate route if an internal
DMA solution fails these proofs. That would require hardware changes; it is
not silently assumed to exist in the current board.

## Host prototype and its negative result

`python research/sync_iq_mux.py` writes `research/data/sync_iq_mux.json`.
It mixes synthetic IQ only in oracle-known blanking, then runs the actual
U85Q-LP host model on the combined stream. One seed, physical ADC model,
PAL/NTSC, C/N 0 and 4 dB, and phase errors 0 / 0.5 / 2 us. This supplies true
camera timing; it is not a real lock detector, live DMA or LCD test.

- At exact timing, active IQ is unchanged, and decoded DAC in the active
  interior (excluding its first 2 us) is also unchanged in these cases.
- Just 0.5 us phase error replaces about **1%** of actual active IQ.
- At 2 us it replaces about **3.9%** and changes some interior DAC values too.

Thus the idea can preserve real snow, but **an independent free-running
raster is insufficient**: it can erase the same weak information we want
to keep. Preserving active input bytes is also not by itself proof of an
unchanged discriminator output. The earlier CPU flywheel measured about
0.5 us per code and could service only about one line in six at a 25% CPU
budget; it must not be re-enabled as an always-fast solution. The older
goggle models' lack of extra luma range does not answer this LCD-muting issue.

## Acceptance before enabling a keeper by default

## Root synthesis and detector prototype (2026-10-13)

The operator clarified that repair must activate only when necessary: clean
camera sync stays untouched, and repair ends once reliable sync returns.
Tracking is a lightweight observer; it does not imply continuous replacement.

`main/sync_synth.h` replaces the archived flywheel's per-sample 64-bit
residual multiply/divide with an exact quotient/remainder walk. It needs no
precomputed full-field raster or large phase-template bank. `test_sync_synth.c`
compares 12,875,488 phase codes, including negative residuals and wrapping,
against the archived expression. All match; invalid intervals write nothing.
This removes expensive arithmetic but is not a physical CPU timing measurement.

An unintegrated draft of the full root flywheel is being tested with unwrapped
25-ns phase detection, whole-pulse admission, actual 4092/32-byte DMA geometry
and the root U85Q-LP decoder at both byte parities. A clean synthetic carrier
is byte-for-byte unchanged. In the first passing pulse-only experiment, every
tested PAL/NTSC H/V pulse survives deep fades and weak reception; the root LP
edge bound is 15 samples (375 ns), rather than the archived raw-DAC checker.

**Earlier negative result:** comparing every true active IQ sample
finds a 1-2-sample overwrite immediately before a vertical interval. Centering
the detector removed part of a systematic early-edge bias but did not alone
eliminate the last sample. Pulse count alone therefore did not establish
content preservation. The corrections and acceptance below supersede this
failed draft; the candidate remains excluded from streaming firmware.

Protecting the full optional 8190-byte watchdog window also left insufficient
write opportunity in some modeled descriptor phases (PAL 1523/1600 pulses,
versus 1600/1600 with the normal 4092-byte observer reservation). A future
integration must either provide another source/consumer geometry or make the
optional black-raster watcher and the real-snow keeper mutually exclusive;
it cannot silently feed synthetic sync back into raw observers. The 512-byte
TX-prefetch guard in this model is an assumption requiring physical validation.

### Physical acceptance

### Nominal and +/-50 ppm camera-clock host candidate now passing

`main/sync_flywheel.{c,h}` is a host candidate, excluded from firmware's
component source list. The first complete clock/provenance candidate passed
PAL/NTSC with the actual root kp-0/U85Q-LP LUT and both pair parities:
all 1600 / 1358 expected H/V pulses in the faded and weak windows, <=15
samples (375 ns) edge error, no clean-carrier edits, exact active-IQ and
decoded-DAC preservation, and unmodified pass-through within half a field
after clean reception returns. CI executes the identical assertions at
0, +50 and -50 ppm source-clock error; no pulse-edge or content tolerance
was relaxed. Exact active-IQ counts for PAL / NTSC are 5,966,595 / 4,933,925
at nominal timing, 5,963,710 / 4,933,700 at +50 ppm and
5,963,705 / 4,933,700 at -50 ppm. Maximum repaired edges in the clock-only candidate across both byte
parities were 13 samples nominal and 14 samples at either offset; the
provenance/coasting corrections below retain the same 15-sample gate. These are
synthetic results, not the affected LCD or a measured CPU deadline.

Corrections retain the observed V-edge offset; field time coasts from a genuine
broad-pulse edge fitted using both edges, rather than following H PLL noise.
Unstable sync/porch slopes cannot relearn output levels, and unstable tips do
not steer the weak-signal PLL. Holdover frequency uses a bounded fit over
qualified edges, with Q16 sample timing instead of Q8. Repair tip synthesis
uses a program-qualified synthetic frequency instead of copying a damaged
RF slope. A universal DAC-zero frequency was disproved by the all-kp audit
below. Test NTSC timing uses exact 572/9 us; the archive's rounded 63.5556
us added a model clock offset. The V-edge uncertainty guard is now 25 ns after independent edge refinement.

The sparse 32-line frequency measurements and stride-three V-pulse width
estimate were the remaining clock defects: a biased V reference could drift
into active picture or move the decoded sync beyond the unchanged edge gate.
Holdover fitting now accumulates already-detected genuine clean fast-path H
edges on every line; only the fit division runs every 32 lines. Level reads
remain sparse. The first and last qualified genuine broad pulses each supply
an independently refined rising/falling-edge midpoint; their mean supplies
the next field anchor, with one 25-ns inward guard. Accumulators reset on
acquisition and phase re-lock. Clean-path detection costs 35.5 / 36.6 modeled
evaluations per PAL / NTSC line, below the existing 40-evaluation test budget.
That proxy does not measure ESP32 execution time or interrupt latency.

**Open gates:** the all-kp follow-up below covers the root 1x LP decoder
in host scenes. Other output settings, source-standard changes, live observer
source freshness, descriptor lifetimes/prefetch and measured CPU timing still
require proof. These results do not prove physical DMA safety or unlimited
source-clock holdover. Normal 4092-byte observer protection passes; the optional
8190-byte black-watcher reservation still fails available write opportunity.
No live integration or default enable is authorized by these host results.

### Provenance, continuous loss and bounded reacquisition

The root candidate now embeds the bounded `sync_repair_ledger.h` interval
ledger. Every admitted complete synthetic write is recorded **before** it
changes the ring, including porch bytes. Detector windows qualify their
entire byte footprint, including the previous phase byte. Acquisition,
normal/vertical detection, sampled pulse checks, tip/porch learning, clock
fits, V-parity decisions and phase re-lock all refuse repaired bytes as
camera evidence. Qualification occurs at bounded function/gap boundaries,
not at every phase-table read. The previous-line re-lock search walks only
original gaps, so it cannot lock onto the keeper's own H pulse.

`sfw_run` now receives two explicit frontiers: `avail_end` is the observer-
protected older analysis ceiling; `rx_complete` is actual completed RX.
Only bytes proved overwritten by the latter expire provenance. Ledger
capacity exhaustion merges all tainted spans conservatively; it cannot
reclassify synthesized input as original. The host fixture also rejects
an impossible RX ceiling, refuses a wholly tainted acquisition histogram,
checks exact wrap expiry and verifies state reset. Normal ring geometry
never exhausts the ledger in these cases. These bounds do not establish
in-flight physical DMA, cache coherency or actual TX prefetch limits.

Loss duration no longer resets the last known PAL/NTSC line/field clock to
acquisition. A six-field pure-noise fixture injects both the former
40,000-line cutoff and `UINT32_MAX - 2` into the lost counter, then returns
a real camera at a 0.37-line phase offset. At nominal and +/-50 ppm, every
holdover H/V pulse passes the same output gate, every active snow IQ sample
and both actual LP parities stay exact, there is still one initial
acquisition, and only the genuine shifted camera produces one re-lock.
The lost counter saturates safely. This exercises the duration-state
transition and approximately 0.1-s noisy holdover, **not** 2.5-s source-clock
drift, temperature drift, standard changes or indefinite LCD lock.

Recovery checks reuse the cheap normal-pulse proof every coasting line;
noisy tips cannot steer the PLL. A wider local search runs at one in eight
lines, and independent original-gap re-lock uses a 16-sample coarse stride
before refining a detected edge and verifying its full width. That stride
cannot skip a qualifying >=150-sample normal sync. This preserves the
existing clean-return and shifted-phase gates while avoiding a full dense
search on every known bad line.

Counters now separate actual nine-step `lowm` calls from extra adjacent
phase steps used by acquisition, level and tip checks. At nominal timing,
the weak fixture falls from 120.3 / 117.5 aggregate budget units per PAL /
NTSC line to 50.5 / 51.3; the latter comprises 36.6 / 36.8 `lowm` calls and
13.9 / 14.5 additional phase steps. Pure loss falls from 153.6 / 152.4 to
62.2 / 62.0 units, comprising 57.1 / 56.4 `lowm` calls plus 5.1 / 5.5
steps. These are whole-fixture averages, including clean intervals and
acquisition. Synthesis, cache maintenance, scheduler/interrupt latency and
transport work are outside these counts. They are **not** proof of a
240-MHz C5 CPU deadline; actual worst-case measurements remain mandatory.

The hot nine-step detector also uses an equivalent branchless phase wrap
and explicitly unrolls its fixed loop. `research/sync_lowm_cost.py` verifies
the wrapping arithmetic and 32,768 random/ring-boundary probes against the
earlier detector. Its isolated pinned RV32 `-O2` probe emits 112 dynamic
instructions rather than 144-153; this excludes the full keeper's extra
instrumentation and surrounding work. The complete PAL/NTSC clock and
content matrix also passes with this arithmetic change.

### Full-pulse synthesis across the active kp ladder (2026-10-14)

The caller now supplies the explicitly verified U85Q-LP program context,
active kp (85, 80, 75, 70, 65, 60, 55 or 50) and its actual LUT words.
Unknown kp, an unverified program or a missing LUT cannot authorize repair.
The caller must provide a consistent snapshot after the existing full
halted-LUT verification and reset the keeper when the ring/sample epoch or
source channel changes. A gain-only write must not be confused with a new
sample-clock epoch; temporarily reject unreliable evidence while retaining
valid holdover timing.
The test chooses that same actual LUT in the output decoder, including both
pair parities; pointer identity across translation units is not used as proof.

A steady carrier sweep was only screening. The earlier -6-MHz universal tip
failed low kp completely. Even screened candidates -5.5 MHz at kp .60 and
-5 MHz at .55 failed some full H/broad transitions after endpoint residual
correction. At .65, -6 MHz failed one -50-ppm camera transition and -5.75 MHz
failed a +50-ppm transition. The candidate
therefore uses this more conservative transition-tested table:

| Verified kp | Synthetic tip frequency |
| --- | --- |
| .85, .80, .75, .70 | -6 MHz |
| .65 | -5.5 MHz |
| .60 | -5.25 MHz |
| .55 | -4.75 MHz |
| .50 | -4.5 MHz |

The real RF plateau and the synthesized cells have different uncertainty.
A conservative 15% circular-phase margin is reserved for real Q4/RF phase
error. In the present strong-source fixture, .60 has intermittent actual
pulse breaks despite a clean raw averaged slope, and .55/.50 corrupt every
checked pulse. Those outputs need repair even with clean RF. The good
actual-output .85-.65 fixtures remain byte-for-byte untouched. This margin
is a host policy supported by these scenes, not a measured RF/LCD safety bound.

Horizontal synthesis compensates the tracker's phase lag at its porch-to-tip
transition, including that phase offset in the endpoint residual. This removes
rare low-kp delayed tips. A first V equalizing pulse has no preceding spare
porch: conditioning real picture before it would erase information. Its short
tip is instead built directly from the actual worker LUT. A bounded 128-byte
transmitted prefix propagates all possible phase states and an upper LP-state
bound for both pair parities. Matching states merge conservatively; nonmerged
states are retained rather than assigning an invented exact phase. Each tip
byte chooses the lowest worker DAC transition against every surviving state,
then ordinary blank DDA resumes. This first-pulse work runs at field cadence;
normal H pulses retain the cheap DDA. Modified prefix bytes describe actual
transmitted decoder history here and never serve as camera-clock evidence.

All 24 kp-by-clock combinations (eight actual LUT levels at 0 / +50 /
-50 ppm) pass the complete host fixture suite. The output test retains
every previous gate: all 1600 PAL / 1358 NTSC
H/V pulses, <=15 samples (375 ns) edge error, exact active IQ and exact LP
output at both parities, real shifted-camera reacquisition and the loss-counter
boundary fixtures. Valid received output resumes complete pass-through.
When the original deep-kp sync remains digitally broken, only its sync still
requires repair; active received content remains exact. On reacquisition,
a genuine fully qualified normal H may be repaired at its observed edge
before another V pulse establishes field phase; malformed or unknown runs
cannot authorize that exception.

The shifted-NTSC test oracle also had a rounding defect: separately rounding
the base edge and truncating the fractional shift made some expectations one
sample early. It now rounds the complete continuous edge. An independent
source test checks actual `deviation()` transitions and V-kind against both
shifted and unshifted PAL/NTSC expectations: the continuous edge is within
half a sample, and the first generated sample on/after it is within one sample.
The repair edge gate remains 15 samples; no error tolerance was expanded.

First-EQ worker/LUT accesses, cell preparation and synthesis are outside the
existing detector counters. Physical cold/prewarmed execution time, stack,
cache maintenance and worst-case scheduling/deadline measurements remain
required before putting this candidate into the live firmware.

The candidate uses a brief recovery qualification and returns to received
sync when that actual program output is reliable. Operator intent is repair only when
necessary, retain real active snow/picture, and return to received sync.

Use the affected LCD and a scope/reference monitor. Capture raw IQ and
terminated CVBS simultaneously through strong startup, gradual attenuation,
short/deep fades, VTX off/return and PAL/NTSC. Establish which layer goes
black. Verify every line/field train, real active-content preservation,
loaded levels, no false sync/replacement, phase drift, and acquisition versus
tracking behavior. Measure end-to-end timing, source freshness, sustained
throughput, CPU/heap/stack and transport fault counts under USB load.
"The monitor never loses sync" requires this physical evidence; host output
or a firmware build alone cannot establish it.
