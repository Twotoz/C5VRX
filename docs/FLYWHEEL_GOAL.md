# Flywheel on the physical C5

Operator goal (2026-10-15): continue until the flywheel works on the C5.
This supersedes the broader active range-audit checklist; earlier evidence
and unresolved RF questions remain historical context in RANGE_GOAL.md.

C5VRX by Twotoz and the C5VRX contributors. The received snow and weak picture
remain the source of active video. Generated black/no-signal content does
not satisfy this goal. The keeper acts when needed and yields promptly to
qualified genuine camera timing.

## Required delivery

- Working, bounded real-stream processing on C5, including CPU/heap/IDLE
  behavior under normal receiver load. A private J test is not delivery.
- Original-source H/V acquisition, loss holdover and selected PAL/NTSC timing
  when no camera has ever been seen. Own repairs never qualify camera lock.
- Whole-pulse writes with independently justified RX/TX lifetimes and cache
  publication; protect all CPU evidence readers and their recent history.
- Real received active samples remain visible through loss. Verify genuine
  return at changed field phase/standard and actual kp/LP/output contexts.
- Actual terminated waveform/monitor acceptance wherever hardware is
  available; separate software counters from display lock. Do not mark the
  goal complete from a host model or a zero-fault private buffer test.

## Current physical state

The C5 was on COM10. Original app and partition/settings bytes were backed up
privately before app-only experiments; existing settings are retained.
The current experimental app is `5.0.0-pr.212-sync-fast`, frozen source
`2d4c422`, app SHA256
`f867ed7df2e2f9384ae224bc6037df1efe778aa3481bb0c206537b681893d969`.
Flash data was verified, forced-download cleared and watchdog boot used.
It is live at A1, LP kp0.85, OUTPUT1x. It still has no live keeper writes.

J measurements are in `research/data/board_j_{o2,prefix,fast}_2026-10-15.*`.
The fast variant gives H preparation minimum24us, maximum190us under the
measured normal load. First EQ minimum1703us and maximum39199us still fail
the streaming deadline; maximum regular V also has preemption outliers.
These extrema do not prove pure kernel cost or WCET. Transport error
counters remained zero and output stayed live throughout these tests.

On2026-10-15 the next app-only flash refused before any download or write:
COM10 no longer existed. Serial enumeration and present-device inspection
showed only Bluetooth COM3..8. Reconnection was requested; this is a physical
measurement dependency, not evidence that the new app runs on the board.
The built `5.0.0-pr.212-sync-profile` app (source87a7f49) has SHA256
`1a77eecc4604fa9fee8646079a9d6f31b71166f47ed3553bfa5eb64227333bf4`.
Its manual W service is read-only, allocates only while enabled, and rejects
late/ambiguous/context-changing turns rather than inventing a camera clock.
J now profiles first-EQ stages without masking synthesis/search interrupts.

Further exact prefix simplification merges equal intermediate tracker phases:
previous DAC is not an input to the next LUT transition. The final ordered
phase/DAC set is retained.19200 prefix and38400 full-tip differential cases
remain identical to cce2eb7;640 profiled pulse cases pass. C5 timing is pending.
The resulting `5.0.0-pr.212-sync-fold` build has SHA256
`40eb3d6a47b0ceaa2e6a71025029abca3801ceb2fca1c4ff552e97865e12bf23`.

## Additional offline evidence

`research/sync_h_bank.py` preserves external waveform gates while moving
H endpoint residual into its48-byte front porch and copying a constant tip.
The original fixed-step candidate fails kp0.50. A separate H-only step-7430
passes the full kp0.50 source-loss/return scenes, all-output transition rows,
and2621440 coupled phase/decoder/output cases. The stronger independent
endpoint/boundary-state fixture finds five kp0.50 rows failing; the unchanged
dynamic synthesizer passes every row. The bank remains unqualified there.
Other attempted frequencies retain their failure evidence; this is not a
live timing or memory-writer proof. See SYNC_PRECOMPUTED_PULSES.md for scopes.

`research/sync_coarse_scenes.py` reuses the exact temporary coarse acquisition
builder and passes all8kp full keeper scene suites. Production acquisition
is still unchanged; bounded TRACK work and actual W timing remain required.
Universal first-EQ preparation is being evaluated separately, including
interactions with the H bank. Individual transition passes are insufficient
when complete fade/return scenes still fail.

## Next concrete steps

### Integrated H period bank

Source `23588c1`, `5.0.0-pr.212-sync-tip-bind`, is flashed/hash-verified as
`7d703bfe9becc522851fcc9cca32b9110ce33ab2dfa074057b7df29d50c2fa4f`.
All16 private J preparations pass with zero transport faults. FirstEQ's best
row is423us; other row minima are about0.8ms under normal preemption. Ordinary
V still has outliers up to36.8ms in this low-priority diagnostic. See
board_j_tip_bind_2026-10-15.json/.txt: this is improved preparation, not live
publication. The user confirmed VTX off during these J tests. With VTX then on,
the ordinary receiver reports NTSC/AFC locked at G53; after VTX off it returns
to G83/AFC unlocked. No flywheel camera-clock claim is made from that control
diagnostic.

The read-service profile `811fdb0` is physically tested in three starts, each
ending before a kernel call:118-142us. DONE observation uses14378-15842 cycles
(about60-66us), source begin5590-6695 and source end3684-5642 cycles. These
measurements justify investigating metadata cost rather than changing waveform
tests. They are retained in board_w_read_profile_2026-10-15.json/.txt.

The first policy image could not allocate J's11136-byte contiguous block on the
C5 despite18432 bytes total free heap; neither J attempt ran. The adapter now
uses a2048-byte private source ring:512-byte start plus1280-byte maximum pulse
and its next sample fit without wrapping. Its9088-byte allocation succeeds.
The smaller source preserves every relevant input byte and all context/cache/
source immutability checks; the actual extracted adapter test covers its bounds.

Source `4dc7e73`, `5.0.0-pr.212-sync-tip-policy-2k`, is flashed/hash-verified as
`879eae189a2129a3e5492d5129e5f28e57752499bca1427e2bcb65793be2fe31`.
Both J runs pass all16 pulses with zero transport errors. FirstEQ row minima
are around1.2-1.3ms, improved from the previous1.7-2.0ms but not halved.
Selection falls to about8k cycles in the least-preempted profile; full OUTPUT
binding now dominates the regular-token stage. Complete evidence is in
board_j_tip_policy_2026-10-15.json/.txt.

The binding helper now checks unity via a complete2048-byte comparison. Other
outputs compute the64 possible DAC codes once, then compare all1024 complete
mapped words, retaining token/tracker validation. The existing7680-tip and
81920-corrupted-word regressions pass. This equivalent helper's C5 timing is
the next measurement; it does not cache an unchecked program context.

The firstEQ singleton selector now uses a generated exact policy indexed by
verified kp, tracker phase and previous raw phase. It preserves the old
minimum-DAC score, phase-distance score and ascending-token tie order. Full
reference and OUTPUT binding are checked before using the policy; masked,
noncanonical and ambiguous-state contexts retain the original minimax path.
The65536 policy choices match the actual-LUT oracle, and38400 whole-tip
differentials remain byte-exact. All eight keeper scene suites, the OUTPUT
extremes and640 private preparations/profile comparisons pass. Physical timing
of this policy is pending; byte equality alone is not a speed measurement.

The ordinary-V image `35e0d8c` is flashed and verified as
`98e03ab1b767b51527c2541b70fb156a1b3d9b651b7f24655dfbdec3b765b73b`.
Both J runs pass all16 preparations with zero transport fault counters. The
ordinary-V observed minimum rows now span29-385us, including first-run
NTSC EQ60us and broad70us, PAL EQ29us and broad74us. Normal preemption still
causes maxima up to24788us; these measurements are not a publication deadline
proof. FirstEQ remains1736-2012us at its row minima before the new policy.
See board_j_period_v_2026-10-15.json/.txt for complete evidence.

Ordinary V now also uses complete periods in canonical unmasked kp0.60-0.85
contexts. A48-byte blank bridge absorbs the endpoint residual, and V blank
uses the qualified-1632 Q8 carrier. FirstEQ retains its existing constructor;
kp0.50/0.55 ordinary V remains dynamic because its unknown-entry-state fixture
has counterexamples. A period-bank match is not a camera-sync observation.
The integrated V passes7864320 independent boundary/output/decoder-state cases,
all eight full keeper suites,640 private preparations/profile comparisons and
the production OUTPUT0.5x/5x extremes. The executor workspace is used directly;
direct host writes need only48 scratch bytes, not another1280-byte pulse copy.

Physical C5 measurement is now available: source `78b0fd4`, version
`5.0.0-pr.212-sync-period-h`, flashed and hash-verified as
`533c7dccce0e4e2242e5c01292a7d71d15bab5aa53085ce4236683a8fb6f88ad`.
Both J runs complete with all16 pulses valid, zero bootstrap refusals and zero
TX-empty/RX-overflow/DMA/BitScrambler faults. Normal-load H minima are24-28us;
these do not establish a material improvement over the earlier24us minimum.
Ordinary V is still326-413us at its observed minima, firstEQ1608-1816us.
The firstEQ profile identifies prefix, regular-token discovery and selection
separately; none of these minimum samples proves live WCET or publication.
Complete measurements are in board_j_period_h_2026-10-15.json/.txt.

The actual manual read-only W service also fails its first80us turn under
normal load:136us, zero kernel calls, service_overrun, and no live writes.
This is evidence against the current scheduling/admission arrangement, not a
successful detector run. The capture is retained locally as
`c5vrx-w-period-h-20261015-a.log`; W is stopped after the experiment.

The H constructor now copies an exact-phase constant tip from generated flash
periods and computes only its48-byte settling porch. Canonical unmasked Phase8
contexts use it; native masks and other phase maps retain the prior constructor.
kp0.50 H uses the separately tested-7430 Q8 step; its old-7373 periodic candidate
failed the boundary fixture. First EQ and ordinary V remain dynamic.

The generated table contains256 strong cells plus264192 carrier bytes, checked
against the actual cell builder and an independent phase oracle. It allocates
no mutable full bank. The integrated production H passes2621440 independent
entry/exit/output/decoder-state cases, all eight full keeper scene suites, and
the kp0.85/0.50 OUTPUT0.5x/5x matrix. Private J preparation and profiling tests
also pass. These establish waveform behavior; C5 timing remains to be measured.

### Current integration (2026-10-15, awaiting C5)

The actual first-EQ constructor now binds the active OUTPUT-mapped LUT to a
complete unscaled reference. Construction scores use the reference while
independent waveform validation uses the actual mapped output. This avoids
rejecting 0.5x output merely because its physical DAC floor exceeds the old
absolute threshold. The binding test checks all1024 words; corrupted token,
tracker or DAC words are refused. The reference regression retains128 exact
unity rows and1310720 actual output decode cases. Full keeper scenes pass at
kp0.85/0.50 and OUTPUT0.5x/5x; see sync_production_extremes.json for complete
stdout and source hashes. These are host results, not a monitor-lock claim.

The production detector now reuses six overlapping adjacent phase steps when
its nine-step window advances by three samples. Its24-byte scratch is cleared
at every service call and synthesis entry, including refused writes. A million
independent comparisons retain exactly the original result; consecutive
windows need three phase lookups instead of ten. Actual C5 timing is pending.

Startup makes RX endless before any TX readiness wait and requests24KiB of
RX/TX separation instead of16KiB. This adds about205us of video latency.
Runtime tests extract the actual startup function and cover descriptor/channel
changes and failure recovery. The requested delay does not establish the
actual separation; live DMA observations remain authoritative.

The new whole-current-and-next-descriptor admission helper models a complete
1280-byte patch with the protected8280-byte CPU history. It accepts the24KiB
fixture over2654208 byte-phase/descriptor-rotation cases at a supplied5us
publication bound. No writer calls it yet. The bound must include admission,
stores, cache operations and ordering, and requires physical measurement.
No live publication or timing flag has been enabled by these arithmetic tests.

The integrated source is committed as `f12bf8f`. Its pinned ESP-IDF6.0.2 build,
`5.0.0-pr.212-sync-rolling`, completes at0x12a5f0 bytes with SHA256
`2e340ea302ad9bbf6cb0e9f35c00a35e41822e969cc7960bac595530b397d030`.
The binary and ELF are retained locally in `c5vrx-sync-rolling-20261015`.
This image has not been flashed: the C5 USB serial device is currently absent.

- Profile first-EQ stages before selecting another optimization. Preserve
  exact pulse bytes: packed states and local token masks already pass
  38,400 differential tips and full kp0.85/0.50 scene tests.
- Measure an actual read-only detector service (W), using completed-DONE
  observations, raw leases and bounded event-driven scheduling. Startup,
  late data and ambiguity must not invent an uninterrupted camera stream.
- Reduce acquisition work with independently tested coarse discovery plus
  precise edge/width qualification. TRACK helper budget overrun remains
  explicit; do not hide it by calling the outer budget a time bound.
- Integrate the protected-history reserve and justify actual memory-write
  admission before enabling any live commit. Prepare/reuse expensive work
  ahead of pulse deadlines where exact boundary/handoff checks permit it.
- Flash tested concrete variants, measure the real stream and validate
  monitor lock through no-VTX, fades and genuine camera return.

### C5 read-service cadence diagnosis (2026-10-15)

The board is connected again. These later measurements supersede the earlier
USB-absent note. All three images below were flashed with application hash
verification; all W runs were read-only, VTX off, with no live writer.

- `408cd05` (`sync-read-clock`): ISR intervals 78-82 us, accepted-capture
  gaps 150-271 us. This separates timer cadence from accepted observations,
  but alone does not prove a task scheduling delay: crossed DONE captures
  are retryable and do not refresh the last accepted timestamp.
- `d37e2e8` (`sync-read-retry`): retry one crossed DONE pair immediately under
  the existing read lease. Two crossings still report UNSTABLE. Actual-source
  tests inject both cases and retain balanced leases and context rejection.
  Separate task-wake measurement finds 137-143 us gaps, with merged alarms.
  Two starts reach one real 64-evaluation acquisition call, then overrun at
  122/124 us; the third fails the unchanged hidden-lap guard.
- `8cb3963` (`sync-read-stack`): remove the per-turn stack high-water scan;
  retain it at cleanup. It scanned several thousand bytes after the measured
  turn, hiding its scheduling cost. Three physical starts now have zero
  coalesced alarms, maximum wake intervals 82/94/91 us, and one real kernel
  call each. Each stops on the unchanged 80 us deadline: total 124/105/116 us,
  acquisition kernel 17383/17653/19166 cycles (about 72/74/80 us at 240 MHz).
  No start proves continuous detector operation or monitor lock.

Complete captured status rows, capture/application hashes and source commits
are in `research/data/board_w_read_{clock,retry,stack}_2026-10-15.{json,txt}`.
The current board image is `5.0.0-pr.212-sync-read-stack`, application SHA256
`00dc2f0d3a5fd7a605cbe9699cbe6d4a1dbf02c291176dc05285a7dfe8cf4e6e`.
All three actual-service/source runtime tests pass in the pinned IDF container.
The immediate remaining bottleneck is the first real acquisition kernel plus
metadata costs, rather than the removed stack scan. Coarse discovery already
uses full-byte phase-table lookups; simply masking IQ bits does not eliminate
those lookups. Any reduced-precision detector needs weak-signal qualification.
The live flywheel/output writer remains unimplemented and disabled.

### Acquisition instruction placement and exact coarse rejection (2026-10-15)

`5db9929` separates the acquisition loop from the large TRACK body and puts it
and original-source provenance checks in IRAM. The ELF confirms `acquire` at
0x4080325a (1126 bytes) and `original` at0x40802fa4 (228 bytes). Three actual
read-only starts run109/4/108 acquisition calls before unchanged deadline
rejection at95/95/97 us. These remain short runs, not sustained lock evidence.

`608f34c` adds exact coarse-window rejection: after each unwrapped phase step,
reject only when even -128 for every remaining step cannot reach the original
nine-step threshold. No IQ bits, thresholds, pulse gates or provenance checks
are removed. Unlike overlapping fine probes, coarse probes do not populate the
rolling cache. One million independent scalar comparisons pass, including
wraparound, altered phase maps and all threshold bins; phase reads total
8002198 rather than10000000. The original million-case rolling test and full
kp0.85 PAL/NTSC keeper scenes also pass after this change.

The exact-rejection image is flashed with verified SHA256
`31c9320ac5732a95650754783d878db0b4edd43815b4ac05ae217da5870856e1`,
version `5.0.0-pr.212-sync-read-exact`. Three VTX-off read-only starts stop at
89/82/102 us total, with1/37/1 actual acquisition calls and maximum measured
kernel7919/10042/8874 cycles (about33/42/37 us at240MHz). No coalesced alarms
or unstable observations occur. These are different received-noise traces and
run lengths; the maxima are observations, not paired speedup or WCET proofs.
Metadata source-begin/end spikes remain material (begin maximum up to10850
cycles, end up to7296). Read timing and eventually live publication still need
work; no live writer or monitor-lock claim is enabled.

Full capture rows/hashes for both images are retained in
`research/data/board_w_read_{iram,exact}_2026-10-15.{json,txt}`.
