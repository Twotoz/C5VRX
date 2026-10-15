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
