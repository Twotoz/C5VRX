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

**Stronger acceptance currently fails:** comparing every true active IQ sample
finds a 1-2-sample overwrite immediately before a vertical interval. Centering
the detector removes part of a systematic early-edge bias but does not yet
eliminate the last sample. Pulse count alone therefore does not establish
content preservation. This draft is not built into firmware or enabled.
Clock/field uncertainty and repair boundaries need further work.

Protecting the full optional 8190-byte watchdog window also left insufficient
write opportunity in some modeled descriptor phases (PAL 1523/1600 pulses,
versus 1600/1600 with the normal 4092-byte observer reservation). A future
integration must either provide another source/consumer geometry or make the
optional black-raster watcher and the real-snow keeper mutually exclusive;
it cannot silently feed synthetic sync back into raw observers. The 512-byte
TX-prefetch guard in this model is an assumption requiring physical validation.

### Physical acceptance

### Nominal-clock host candidate now passing

`main/sync_flywheel.{c,h}` is a host candidate, excluded from firmware's
component source list. `test/test_sync_flywheel.c` now passes the nominal
PAL/NTSC cases with the actual root kp-0/U85Q-LP LUT and both pair parities:
all 1600 / 1358 expected H/V pulses in the faded and weak windows, <=15
samples (375 ns) edge error, no clean-carrier edits, exact preservation of
5,966,595 / 4,933,925 active IQ samples and their decoded DAC samples, and
unmodified pass-through within half a field after clean reception returns.
These counts are synthetic evidence, not the affected LCD or a CPU deadline.

Corrections retain the observed V-edge offset; field time coasts from a genuine
broad-pulse edge fitted using both edges, rather than following H PLL noise.
Unstable sync/porch slopes cannot relearn output levels, and unstable tips do
not steer the weak-signal PLL. Holdover frequency uses a bounded fit over
qualified edges, with Q16 sample timing instead of Q8. Repair tip synthesis
targets the root LUT's known DAC-zero frequency instead of copying a damaged
RF slope. Test NTSC timing uses exact 572/9 us; the archive's rounded 63.5556
us added a model clock offset. A 50-ns V-edge uncertainty guard is included.

**Still failing:** explicit +50 and -50 ppm camera-clock probes preserve all
tested pulses but can overwrite 1-2 active IQ samples at a field boundary.
The latest all-edge frequency fit also exposes pulse-edge errors in an offset
case. Nominal tests therefore do not authorize live integration or default
enable. Source-clock uncertainty, arbitrary kp/output settings, prolonged
loss, false self-observation, descriptor lifetimes/prefetch and measured CPU
timing remain required gates. The legacy 2.5-s reset also remains in the host
candidate and must be replaced with tested ongoing holdover/reacquisition.

The candidate implements a brief recovery qualification, not continuous
sync replacement on a good camera. Operator intent is repair only when
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
