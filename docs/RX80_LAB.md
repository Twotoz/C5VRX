# RX80 hardware lab

C5VRX by Twotoz and the C5VRX contributors. This work extends the earlier
F80/F160 MODEM_DIAG/PARLIO experiments in
`legacy/c5vrx3/docs/continuous-iq-findings.md`, and the FIFO/throughput
investigation in `legacy/c5vrx2/measurements/pr16-linear80/`.

Objective: establish a real, usable 80-MS/s acquisition and receiver path on
the connected XIAO ESP32-C5, rather than report a requested clock as a result.

## Starting state, 2026-10-30

- Windows COM10, USB VID 303A / PID 1001.
- Running image: PR293, commit `e176a8035a54e98b9cce04e54a270e2a3741943d`,
  printed ELF prefix `02e559384`, ESP-IDF v6.0.2.
- A1 / 5865 MHz, P64, RX divisor 6, TX40, no detected video in the initial
  status observation. No RF/video performance claim follows from that state.
- Dedicated worktree/branch `C5VRX-rx80` / `lab/rx80`, based on PR293 head
  `6aa757b` when the bench was added. Original user's other worktrees untouched.
- Original flash `[0,0x310000)` saved locally as
  `research/data/rx80/original-pr293-flash.bin` (3,211,264 bytes), SHA-256
  `587228e766e4a8c79088aedded011c721eba0f7be7a30f4484392f59fd8302cd`.
  Includes original bootloader, partitions, NVS and factory app; excludes the
  flight log. Treat this device-specific backup as a local recovery artifact.

## First diagnostic

Console `0` pauses control, measures RX descriptor progression with TX on
and off at integer PLL240 divisors 6/5/4/3/2/6, restores the saved divisor,
and restarts the verified live transport. Each observation masks interrupts
for 2 ms to avoid hidden ring laps. Sequential descriptor transitions and
their lengths provide bytes moved; CPU cycles between the first and last
observed boundary provide elapsed time. A
nonsequential/invalid transition invalidates the observation. This is a
throughput probe, not a byte-exact sample-continuity proof. No CPU copying or sample processing
paces the stream. Different RX/TX rates are temporary bench conditions.

Remaining gates: measured 80-MS/s ingress, sample integrity/continuity,
sustained DSP/DAC throughput with no overflow/underflow, receiver/video
validation, and evidence recorded with exact firmware identity. No gate is
declared passed by a successful build alone.

## First board result

Lab A ELF SHA-256
`06286be5a11a06b03bc4f8850abbadc1c11e157e3443d00ca8be098ff073dde4`;
printed version `5.0.0-rx80lab`, commit `unknown` because the worktree's
Windows gitdir path is not available inside the build container. Subsequent
builds pass the lab identity explicitly. App-only flash check passed with
matching ELF, no chain faults, heap minimum 1352 bytes, no detected video.

Two console-0 trials with PLL240 source selector 2 and CPU240 found the
same throughput ceiling with TX on and off. Lab A's received div3 records
(nominal 80 MHz) were approximately 40.003/40.008 MB/s (TX on/off) in trial
1; the RX overflow flag was set. Trial 2's descriptor-boundary estimate
varied around 39-41 MB/s; the longest full-descriptor intervals remained
about 24,650 CPU cycles (102.7 us for 4092 bytes). This is **negative evidence
for a simple RX clock-divisor change**, not successful 80-MS/s capture.

Several printed records were missing in both trials, so these logs are
incomplete sweeps. Lab B adds USB draining time between records, reports
the descriptor geometry and clears FIFO status separately for each trial.
It also compares AHB40 against AHB48 (root /6 versus /5), inside the clock
HAL's documented `f_ahb <= 48 MHz` and integer CPU/AHB-ratio envelope.
Both dividers are restored before the live receiver resumes. This tests
whether the ceiling tracks the AHB byte interface; it makes no 80-MS/s claim.

Lab B ELF: `2774767e4d6baefb7eb176f472c05722a151e992beb33b01be3309e301e15646`.
Both flash/boot health checks passed. Its nominal AHB48 trial is **invalid as
an AHB-frequency comparison**: review of `clk_tree_ll.h` and `rtc_clk.c`
showed that `PCR.bus_clk_update.bus_clock_update` must be strobed after the
divider write. Lab B omitted that strobe. Its unchanged ~40-MB/s observation
therefore does not rule out bus clocking as the limiting factor.

Lab C adds that hardware update handshake (bounded to 1 ms), restores the
original AHB clock through the same handshake, and measures whole ring laps
between identical descriptor transitions. Whole-lap byte counts remove
endpoint ambiguity due to prefetch and the two short tail descriptors (the
driver's actual geometry is seven 4092-byte nodes plus two 2062-byte nodes,
cyclically rotated). Its observation window is 4 ms; per-record drain time
is increased to 100 ms because some Lab B sweep records were still missing.

Lab C ELF: `a66d033ec91a56dbf0c02b590e65b591d0e0bcc282228631a1e2c5f29f5149fa`.
Flash/boot health passed; both trials ended with `ahb_restored=1 live=1`.
The whole-ring measurements now track the activated AHB clock:

| AHB | Requested RX clock | TX | Ring-lap byte rate | RX overflow |
| --- | --- | --- | --- | --- |
| 40 MHz | 20 MHz (/12) | on | 19,998,931 B/s | no |
| 40 MHz | 40 MHz (/6) | on | 40,000,966 B/s | no |
| 40 MHz | 60 MHz (/4) | on | 40,000,915 B/s | yes |
| 40 MHz | 60 MHz (/4) | off | 39,999,084 B/s | yes |
| 48 MHz | 60 MHz (/4) | on | 47,999,414 B/s | yes |
| 48 MHz | 60 MHz (/4) | off | 47,997,949 B/s | yes |
| 48 MHz | 120 MHz (/2) | off | 48,000,878 B/s | yes |

All received rows had `bad=0`. Some long records still disappeared from USB;
the table only uses complete received records. No missing record is a pass.
This establishes the AHB-dependent byte-rate ceiling in this configuration.
The 48-MHz path is faster than 40, but still overflows for an 80-MHz input;
it does not satisfy the goal.

Lab D shortens each USB rate record and adds explicit bench keys 2/3 for
AHB60/80. These exceed the vendor clock HAL's 48-MHz constraint and are
**overclock experiments**, not supported receiver settings. To limit scope,
each AHB clock change is confined to a 4-ms interrupt-masked window; the
original AHB and RX clocks are restored before printing, task scheduling,
or any flash operation. No overclock setting is persisted or applied at
boot. A positive short byte-rate result still requires source integrity,
sustained operation and a usable video pipeline before completion.

## First positive 80-MS/s acquisition result

Lab D ELF: `897eb076fba29e83ecc88ba22900f608d6645663b73d751945aa7f8ec5425ab6`.
App-only flash/boot health passed. Whole-ring observations on the real
MODEM_DIAG Q4/I4 input (eight-bit bytes, original lane and pin routing):

| AHB | RX divider / requested rate | TX | Measured ring-lap rate | RX overflow |
| --- | --- | --- | --- | --- |
| 60 MHz | /4, 60 MS/s | on | 59,998,169 B/s | no |
| 60 MHz | /3, 80 MS/s | on | 59,997,253 B/s | yes |
| 80 MHz | /3, 80 MS/s | off | 79,991,455 B/s | no |
| 80 MHz | /3, 80 MS/s | on, repeat | 80,003,526 B/s | no |
| 80 MHz | /3, 80 MS/s | off, repeat | 80,011,123 B/s | no |
| 80 MHz | /2, 120 MS/s | on | 79,995,388 B/s | yes |

Each byte is one complex Q4/I4 sample; this result does not reduce IQ bit
depth. All listed observations had `bad=0`; all trials returned with
`ahb_restored=1 live=1`. This supersedes the old unqualified practical
40-MB/s ceiling for this short, overclocked RX experiment. It does not
change the vendor clock constraint or prove a supported/stable live mode.

There are still missing USB records (native nonblocking console); only
received complete records above support claims. Lab D does not prove
sample-by-sample integrity, long-term stability, or matched RX80/TX80 video:
its TX stayed at the existing 40-MHz geometry.

Lab E adds console `4`: a ten-second continuously clocked RX80/AHB80
acquisition stress gate, with TX40 explicitly retained. Control is paused;
the logger flash mutex prevents flash writes during the temporary bus
overclock. Interrupts and tasks run between short ring-lap rate windows.
The original clocks and verified live chain are restored afterwards.

### Ten-second acquisition stress, Lab E

ELF SHA-256: `038d6372b35fec425236a8058faaa0bbe82c4ccc66928ec6cd3b3b3e6f0fa7f8`.
Flash/boot check passed. Both ten-second runs completed without reboot:

| Trial | Continuous run | Rate windows | Bad transitions | Mean byte rate | Observed bytes in measured laps |
| --- | --- | --- | --- | --- | --- |
| 1 | 10,000,778 us | 1923 | 0 | 79,996,178 B/s | 552,370,176 |
| 2 | 10,005,791 us | 1924 | 0 | 79,996,032 B/s | 552,632,320 |

Both returned `raw=0` (no PARLIO RX overflow or TX empty), `dma_raw=3`
(RX completion bits only; the production error mask `0xfc` is zero),
`entered=1 restored=1 live=1`. The bus and RX stayed at 80 MHz throughout
each ten-second interval, including the yielded task/IRQ intervals; only
rate observations were interrupt-masked. The measured-byte totals count
complete laps inside those observations, not all samples during the run.
No byte-perfect RF-source claim or usable-video claim is inferred.

### Digital integrity oracle, Lab F (pending hardware result)

Console `5` temporarily substitutes the PARLIO TX signals for MODEM_DIAG
on the same eight IQ GPIO routes. A 4080-byte cyclic DMA frame contains 16
complete maximal-length PRBS8 periods (polynomial mask `0xb8`); TX
BitScrambler is bypassed. This extends the earlier C5VRX pad/address
oracles, now with a 40-MHz control and a matched 80-MHz TX/RX case under
AHB80. Completed RX descriptors are frozen and checked against the PRBS
recurrence across multiple TX frame wraps. Six receive-phase positions are
tried. The original IQ route, clocks and verified live chain are restored.
This tests digital transport, including the GPIO27 LED route, rather than
MODEM_DIAG/RF clock alignment or analog video quality.

## Operator target and live controls

Operator reports a clean FPGA result from the same 4-bit I / 4-bit Q input
at 80 MS/s, versus noisy C5 video at 40 MS/s. This is user-reported hardware
context, not a measurement made by this lab. Preserve full Q4/I4 resolution
and make the demodulator process every input sample. Requested UI: an
EXPERIMENTAL 40 / 48 / 80 MS/s choice (40 default), changing acquisition,
bus clock and decoder together with verified recovery. Menu/runtime
integration is present in the lab firmware; live image acceptance is still
outstanding. The operator reports 48 MS/s very noisy and 80 MS/s black.
This negative result takes precedence over passing digital loopback tests.

The VTX heats up. Keep it OFF during builds and analysis; ask for it ON
only when the firmware is flashed, checked and ready to start immediately.
The first live trial is shortened to nine seconds, then baseline recovery.

## Native PAIR80 counter demodulator, Lab G

ELF SHA-256: `94993b381e6b22e94b7a5e4776333b0ec0dd0273587d070021c6e1838ab1878d`.
`research/rx80_counter.py` generates `main/rx80_counter.bsasm` and its oracle
header. It extends C5VRX's Phase8 HR/PR90 and high-byte-counter routing work;
it does not need simultaneous RX/TX BitScramblers. The archive establishes
that those directions share a half-duplex engine; the earlier two-engine
idea is therefore not the selected implementation.

One lookup converts each full Q4/I4 byte to an angle term. Alternating
ADDCTIAH / LDCTIAH operations recover adjacent **nonoverlapping pair**
differences using Counter A. Every raw byte participates, with one read8
per instruction. At input 80 MS/s the pair spacing is 12.5 ns, unique
video is 40 MS/s, duplicated to physical TX80. It is not an 80-million-
unique-output/all-adjacent discriminator. The four replicated LUT banks
allow raw lookup and high-byte counter arithmetic in the same instruction.

Phase period 320 and bias 24 give approximately 4 DAC codes/MHz, linear
over [-6,+9.75] MHz; outside that window the six-bit code wraps, rather
than clamps. This known limitation needs carrier/noise/image evaluation.
LUT SHA-256: `08f2ebb646ebed767540ad12c2713b1002e73457d29c668f680cf92c68467279`.

The IDF-assembled mux/opcode emulator passes all 65,536 ordered raw pairs.
Console 6 then compares actual TX pad data with the independent pair
reference in the PRBS8 oracle: all six 80-MHz RX phases passed with zero
errors and no FIFO faults (24,546 / 22,516 / 22,516 / 20,486 / 20,486 /
22,516 compared byte transitions). The 40-MHz control also passed all six
phases. This establishes bounded native demodulator transport at 80 MS/s,
not a clean live RF/video result.

The hardened direct-byte oracle (console 5) explicitly rejects PRBS8's
excluded zero fixed point. Its repeated 80-MHz run passed all six phases
with zero errors (five 20,486-byte checks and one 24,546-byte check).

VTX-on baseline before the live build: A1 5865 MHz, NTSC detected, AFC
locked with requested +232-kHz correction, G17, no transport faults. The
VTX was requested too early for that baseline; future ON windows wait for
the flashed/checked test image. Console 7's pending nine-second test uses
real MODEM_DIAG IQ80 -> PAIR80 -> TX80, holds the established gain/DC/AFC,
tries three RX phase positions and restores the verified IQ40 path. The
normal production observers are not yet rate-aware, so they stay paused
for this isolated live diagnostic.

## Menu and native timing investigation

The menu path was read back on the connected board, not inferred from its
label: `mode=2 acquired_hz=80000000 ahb_div=3 rx_div=3 tx_div=3 verified=1`.
RX/TX both divide PLL240 by three; returning to 40 restores all dividers
to six. `acquired_hz` is the configured rate, not an independent throughput
measurement. Earlier bounded throughput tests supply the latter evidence.
The live menu transport reported no overflow/DMA/BitScrambler faults, but
this does not prove good RF samples or usable video.

APB is now divided by two at AHB80 (APB40); only AHB needs the experimental
80-MHz setting. The flash-log reset audit showed reset reason 1 (power-on),
not a proved watchdog reset caused by RX80. The reason for the observed
uptime drop remains unresolved.

`native-phase-01.log` measured the actual MODEM_DIAG signal with gain/DC
held, TX stopped, and frozen completed RX windows. At 80 MS/s, fourth-
difference excess was 94--118 in several positions but 2022--2032 in
three other positions. This is strong evidence of phase-dependent capture
quality, but stopping TX/RX limits its relevance to normal live video.
Console 9 therefore probes guarded completed raw windows with RX80/TX80
both running, without freezing RX. It is a manual diagnostic, restores
the normal chain afterward, and does not yet select a production phase.

At 48 MS/s, sampling an 80-MHz sample-and-hold source is also nonuniform
in source time: selected source indices advance by 1/2/2 samples, although
the decoder assumes uniform 20.833-ns intervals. This is a timing-model
concern, not a measured explanation for all the operator's noise.

### Subsequent boot failures (goal remains open)

The later `live-phase-vtx-on-01.log` filename is misleading: the operator
explicitly corrected that the VTX was OFF. It contains no accepted RF-on
measurement. It does expose a different boot state: reset reason 4, fault
latched, no RX descriptors, and RX/TX dividers still six despite the requested
PAIR80 mode. The bus-update stage timed out after about 1 ms. Thus earlier
successful menu readback does not describe every later boot; a saved mode
must not be mistaken for the running hardware rate.

Changing AHB with APB undivided, then applying APB /2 in the same bounded
critical section, gets beyond that failure in the next flashed build.
The startup now first establishes a verified 40-mode recovery target, then
attempts the saved experiment. This exposed a separate reproducible panic:
`receiver_start` could not allocate the 3072-byte `gain_fast` task after
the splash raster had consumed heap. The next revision starts essential
receiver/control tasks before the optional splash allocation and avoids
persisting the temporary bootstrap rate. Neither change establishes RF
image acceptance. The board was explicitly placed in download mode while
repairing the reboot loop; the VTX stays off.

The repaired image (ELF prefix `925422e36`) passes the flash check and a
second ROM-mediated reboot with saved RX80, RX/TX/AHB divisors three,
live transport, nine RX descriptors and no reported transport faults.
The optional boot raster uses its existing progressive fallback when the
interlaced chain cannot fit after essential task stacks. These are startup
checks with VTX off; phase qualification still reports unsettled/high
excess in some contexts. They do not validate a clean image.

### IQ80 AFC observer view

The first observer revision selected every second sample of the copied
8190-byte IQ80 snapshot in place, after native-rate picture decoding. That
view covered only 1.6 lines, so B/W timing confirmation missed windows
without two complete syncs, and `s_last_video_us` (gain-map learning and
the RX80 controller's `carrier_recent`) lapsed with it.

Revision 2026-10-31: `copy_snapshot40` copies 16380 captured bytes
(204.75 us) and decimates them while copying, giving the AFC the same
8190-sample, 3.2-line IQ40 window as at RX40. Still no second window
buffer. It keeps the completed-ring rule at the native rate (~150 us before
RX could reach the copied bytes). The picture measure now runs after the
AFC on its own native-rate copy. Acquisition and the DAC path remain
IQ80/TX80. IQ48 still holds AFC, since integer decimation cannot map it to
IQ40 (see "IQ48 video evidence" below).

`test_rx80_afc.c` generates actual 12.5-ns FM phase steps before observer
decimation. All 240 tested colour windows (PAL/NTSC, both polarities,
twenty start phases, -1/0/+1 MHz offsets) recover the expected polarity,
porch and sync span. With the 1.6-line view the test accepted >= 80 %. The
full window must accept all of them. All 64 independent noise windows are
rejected. The decimator is also checked on aligned and unaligned ring
segments. This is synthetic observer validation, pending the board/VTX check.
The copy time against the ring-lap deadline is not yet measured on the board.

## Captured duplicate-source fault and H40 demod correction

The operator reports very brief stable intervals with exceptionally sharp,
clean video, surrounded by severe glitches/noise. This is positive visual
evidence of those intervals, not stable RX80 acceptance. In the next short
VTX-on test the input observer detected NTSC while transport counters stayed
fault-free. A direct console snapshot was refused at a gain transition; the
flight log retained CRC-valid snapshot 3035, session 331, t=58305 ms, G17,
A1/5865, PAIR80-C320. Its 8128 raw bytes are **80-MS/s capture data**, not
the reader's former hard-coded IQ40 metadata.

Every odd-aligned neighboring pair in that snapshot is identical (4063/4063),
whereas even-aligned equality is 60.7%. It contains real horizontal syncs.
The original C320 demod sometimes differentiates held copies, sometimes
samples separated by 25 ns while assuming 12.5 ns. Its frequency gain is
then wrong, and 53 of 4064 pair outputs wrap around the six-bit window.
This is a concrete error in the prototype's source timing assumption.

Live source-cadence probes with VTX off, frozen gain/DC, guarded raw copies
and RX80/TX80 running confirm an almost perfectly held alternate pair at
all four existing DFILT modes. Both pinned vendor ADC selectors, both RX
edges and APB40/APB80 retain this structure. The ADC probe verifies exact
full-word MMIO and I2C restoration; the production default is retained.
This revises the historical assumption that the GPIO tap necessarily
supplies 80 million fresh IQ values merely because the RF writer advances
at roughly 80 MS/s. The actual source-clock/tap cause remains unresolved.
The two vendor-used source-mux codes did not establish a different source:
code 1 read back as zero, so the candidate was not promoted as a new format.

The menu's corrected experimental 80 path uses **PAIR80-H40-C256** (wire
decoder ID 6), ELF prefix `51a286edb`. It keeps RX80/TX80, Q4/I4, the raw
ring and hardware-owned pacing. Lookups select raw endpoints 0,2,2,4,...,
so emitted differences are (2-0),(4-2),...: overlapping 25-ns slopes at
40 million unique DAC values/s, duplicated at TX80. Either alignment of
a held40 source gives the same transfer. Period 256 and bias 24 yield
6.4 DAC codes/MHz; the unclamped wrap window is [-3.75,+6.09375] MHz.
This handles the observed held source rather than falsely claiming 80
million independent source samples. The original real-fresh80 target
remains open; H40 is an interim demod correction, not completion of it.

Validation:

- The actual IDF-assembled instruction emulator passes all 65,536 endpoint
  pairs in both duplicate alignments. The previous native80/48 programs'
  exhaustive checks remain passing.
- Hardware console V tests H40 against independent PRBS endpoint slopes:
  all six RX phases at 80 pass with zero errors (20,486 compared byte
  transitions each), as do all six 40-MHz controls. No FIFO faults.
- Replay of RF snapshot 3035 has zero wrapped H40 outputs, versus 53 with
  the old prototype. The production software picture measure finds one
  sync (one expected) with H40, versus zero with C320 on the same bytes.
- Flash/ELF/startup checks pass; a sustained VTX-on image check is pending.

Flight-log readers now derive snapshot capture rate from the verified
decoder identity, including IDs 4/5/6, instead of exporting every capture
as 40 MS/s. This metadata describes capture cadence, not source freshness.

## Remaining sync loss, gain observations and RX80 AGC

The operator found H40 more stable but still losing line sync frequently.
One subsequent twelve-second log was recorded **before** the operator
confirmed VTX power and is not an RF-on test. The confirmed-on repeat made
three snapshot attempts, of which only one succeeded (G25, FNV `a5426a79`).
It still contains one negative phase excursion beyond H40-C256's -3.75-MHz
limit, wrapping a sync-tip sample into a high DAC code.

**PAIR80-H40-C256-B32**, wire ID 7, moves bias from 24 to 32 while retaining
6.4 codes/MHz and the same cadence. Its range is [-5,+4.84375] MHz and its
blanking/sync levels better match the original DAC levels. Replay has no
wrap on the new capture; assembled exhaustive tests and all twelve bounded
PRBS controls (six 40, six 80) pass. ELF prefix `062ad24c9` was flashed and
checked. The operator reports that frequent sync loss **still remains**.
Removing that concrete wrap error does not explain or solve the whole fault.

The gain probe also still used four 64-raw-byte regions as if acquisition
were IQ40. It now reads four 64-observation-byte regions from a guarded
8190-byte IQ80 history, selecting every second raw byte. This preserves
both the region durations and their ~102-us spread, without an 8-KiB
allocation. The sentinel likewise takes 64 observations from 128 raw bytes.
Production-code host tests pass at 81 ring/descriptor discovery positions
and reject stale, lapped and malformed reads. The video path is unchanged.

A 4-s automatic / 8-s fixed-gain test (console E) in session 339 held G29
through seconds 68--74 with **zero gain writes**, yet picture sync coverage
was 23--75% and clipping was present. G33 in the final console line occurred
after automatic mode resumed, not during the hold. Earlier G17 intervals
had no clipping and roughly 80--92% coverage. Thus the selected G29 was too
high for a definitive clean fixed-gain comparison. Operator feedback was
"still much sync loss", with only a tentative reduction in high-gain
artifacts. Do not record the latter as established visual acceptance.

At the operator's request, the separate experimental **RX80** automatic
gain mode (new code 252) uses the existing gain owner and calibrated row
steps. It targets headroom for the counter discriminator: attack on raw
clipping/large p95, allow settling, require 60 ms of continuously weak
probes before release, and hold release 200 ms after attack. It retains the
existing one-sided-DC ownership. The first on-board VTX-off check exposed
hunting: 3-dB release steps jumped across the p95 hold band (~2.5 dB wide).
Release is now 1 dB per eligible step, with a closed-loop plant regression
that rejects that limit cycle. Weak/absent input still reaches the table
maximum in the synthetic bound; this is not RF sensitivity/range evidence.

Entering 80 from an automatic model selects RX80; leaving restores that
model. An explicit model selection or fixed-gain bench is respected. The
40-mode model/cycle stay unchanged. Owner-routing, gain-map/context and
controller tests pass. The fine-step RX80 build is pending board acceptance;
stable video and the original fresh80 goal remain unachieved.

### Current RX80 model iteration (2026-10-31)

Further off-air runs exposed the historical DC-floor failure shape as well:
the first fine-step controller repeatedly returned to G52 from a static,
high-offset row. RX80 now preserves one-sided/DC-point ownership. Below
the calibrated DC region it may approach that region in bounded nominal
1-dB steps only without recent video evidence; a stationary picture level
must not cause that escalation. Entering RX80 during boot/paused ownership
reasserts the current gain after the DC hook is installed, so a valid row
is actually held before envelope decisions. Controller steps use the current
row gain model; their physical dB values are not independently measured here.

The old one-microsecond RX-divider pulse can repeat the same phase at
PLL240/3. RX80 phase requests now use the short bounded cycle pulse already
tested in the native RF bench; the collector must still qualify the result,
not assume a deterministic single-position move. Spectral observers now
use 40 MS/s for the normalized probe view, rather than incorrectly treating
that copied IQ40 view as IQ80. Capture/DAC clocks remain RX80/TX80.

Current flashed ELF prefix: `5d6c9f422` (`flash-rx80-agc-phase.log`). Startup
and transport checks pass, with min internal heap 1220 bytes and largest
free block 17408. At 52 s with no confirmed VTX the model remained near G82,
but counters still showed 35 attack and 70 release requests: the initial
extreme hunting is reduced, not eliminated. Do not call this stable RF gain
or accepted video. The next confirmed-on twelve-second observation is
pending. The operator's fixed-gain feedback was subsequently qualified as
only **possibly** fewer high-gain artifacts, with frequent sync loss still.

Pure controller tests cover overload/settling/stale samples, short scene
dips, nominal 1-dB release, the closed-loop coarse-step limit cycle,
one-sided/DC-point handling and managed-floor/recent-video separation.
Production routing and ring-copy tests pass; original gain-map context
tests also pass with the added model state. These tests do not establish
new RF range, source freshness, analog DAC waveform quality or stable sync.

The next confirmed-on run (`rx80-agc-phase-confirmed-on-01.log`, session
343) still has frequent sync loss and many gain changes with no transport
faults. Attack-event payloads reveal an overly aggressive new-model gate:
most attacks have P95 exactly 45, clipping zero, and no reached rails,
across gain rows 16--40. This is not proof of excess RF amplitude. The
draft-PR revision raises the power-only threshold to 65 (roughly the
8-cell quantizer edge), retaining the separate clipping/DC guards. A
regression replays those measured unclipped P95 values and requires no
attack. This last correction has host validation; RF acceptance remains
open. The PR must stay experimental/draft until that gate is passed.

### Floor release and video evidence (2026-10-31, code review)

The code review found no board log for this, but it did find a feedback
path. The managed-floor release (`dc_point`, no recent video, row below the
DC floor) skipped the attack branch. A strong carrier clipping on one side
(`clip_pm` up to 1000) therefore climbed 1 dB per >= 60 ms whenever video
evidence lapsed. At 80 MS/s it lapsed often (the 1.6-line AFC view above),
and the clipping itself suppresses video, which closes the loop. The floor
release now requires `clip_pm < 100`, the receiver's own "real clipping"
threshold. Such a window holds, and the receiver's DC-urgent request owns it.
`test_rx80_agc.c` replays the shape: 2 s of one-sided 30 % clipping without
video, with no release. The PR still records sync loss at fixed gain, so
this addresses gain hunting, not the remaining sync loss.

### IQ48 video evidence

The 48 MS/s path has no AFC observer, so before this revision
`s_last_video_us` never advanced at 48. Gain-map learning stayed off, and
menu, diag and calibration read "no video". There, the rate-scaled native
picture measure now provides the evidence when it finds both expected line
syncs of a settled window (8190 bytes = 2.7 lines). This is weaker than the
AFC's burst/porch confirmation and never drives a frequency step. 48 keeps
the 40-MS/s LEVEL/KEEL models. Their measured quality table is for the IQ40
Phase8 decoder, not the 48 counter decoder, so no 48-specific gain
optimum is claimed.
