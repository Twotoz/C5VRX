# Bounded integration of a real-snow sync keeper

C5VRX by Twotoz and the C5VRX contributors. This design is grounded in root
`main/transport.c`, the existing raw observers and the host flywheel. It is
an implementation plan, not completed live firmware or measured LCD lock.

## The smallest streaming topology change

Keep RX and TX descriptor chains, U85Q/U85Q-LP instructions, DMA throughput
and active IQ intact. One keeper task observes completed raw IQ and patches
only admitted sync intervals in the shared raw ring ahead of TX. No full
field buffer, per-pixel CPU decode or descriptor rewrites are required.
Normal reception runs detection without writes. Repair activates after
qualified missing/malformed sync, continues while needed, and returns to
received sync after bounded qualification. Raw-source provenance accompanies
every patch; it is never used as recovered camera evidence.

This is viable only if the following descriptor and CPU guards pass. The
current host prototype does not yet provide those hardware proofs.

## What the current ring actually permits

Both streams use PLL_F240M/6. RX starts first; `start_rings_tail()` waits an
integer 409 us, then starts TX. Driver setup/start latency adds uncertainty.
The nominal separation is approximately 16 KiB, not an invariant literal
16384 bytes. Owned TX uses8 x4092 bytes and a32-byte tail. RX uses the
pinned IDF driver's independently mounted geometry. Its `parlio_rx.c`
`parlio_rx_mount_transaction_buffer()` splits a final remainder between two descriptors
when it exceeds4092bytes but is less than8184bytes. For a32768-byte aligned
body this is seven4092-byte nodes and a final pair whose sizes depend on
transaction alignment:2060+2064 at4-byte alignment, or2048+2076 at32-byte
alignment. The macro rounds the first half down to that alignment. This
is a source-derived expectation; the actual hardware probe must report
its discovered node lengths. It differs from the owned TX geometry.
Discovery may begin at any node. Every descriptor is its actual interval;
index times4092 is not a valid general RX position.

The existing `in_dscr_bf0`/`out_dscr_bf0` registers identify descriptors,
not a proven byte consumed within them. Before enabling writes, establish
their semantics against the IDF/TRM and actual hardware. A completed RX
boundary is the start of its current descriptor only if that register
really names the current in-progress descriptor. A conservative TX frontier
is the **end** of its current descriptor plus a verified bound for bytes
already prefetched into GDMA/BitScrambler/PARLIO. The host's 512-byte guard
is an assumption; declaring it constant does not prove it.

The pinned IDF's ESP32-C5 `soc/ahb_dma_struct.h` does explicitly document
`in_dscr_bf0` as current RX descriptor x already fetched, `out_dscr_bf0` as
current TX descriptor y already fetched, and `out_dscr` as the next y+1.
Its RX/TX L1 FIFO count fields are seven-bit **byte** counts. This supports
the descriptor interpretation, but does not by itself bound the entire
prefetch path, queued input, outstanding burst or register-sampling skew.
`soc/parl_io_struct.h` also exposes 19-bit RX FIFO written/read bit counters
and a TX FIFO read bit counter. They can help measure backlog/actual lag,
after confirming reset/wrap semantics on the continuous root stream.
The current public [ESP32-C5 Technical Reference Manual](https://documentation.espressif.com/esp32-c5_technical_reference_manual_en.pdf)
download identifies itself as Version 1.1. Chapters 5, 43 and 44 document the
GDMA, PARLIO and BitScrambler architectures, but the inspected architecture
sections do not establish a complete 512-byte prefetch bound. A seven-bit
L1 FIFO count alone is insufficient to establish all downstream/in-flight
buffering. No hardware size is inferred from that count's bit width here.

Use actual descriptor addresses and validated buffer/length/next pointers
to unwrap independent monotonically increasing absolute RX/TX coordinates.
Require the observed producer to be ahead of consumer by less than one ring
and by enough bytes for the entire next patch. Reset these coordinates on
every transport generation. A poll gap of one 819.2-us ring or more cannot
be disambiguated by descriptor indices: refuse writes and establish a fresh
epoch. Byte-rate/time bounds also reject an unobserved lap even if indices
look unchanged. Do not deduce absolute laps merely from modulo subtraction.

`main/dma_progress.h` now provides this pure progress adapter. It validates
the complete contiguous 32-KiB layout and physical address links, including rotated
discovery. RX/TX layouts are independent: nine unique aligned nodes, each 4..4092
bytes, one full ring total and at least one 4092-byte observer node. It
bootstraps a shared **relative** data epoch from RX/TX BF0
captures. It never claims the number of hardware laps before that capture.
The current RX descriptor and all older startup IQ remain unqualified;
`source_fresh_from` begins at its end. Every rebootstrap gets a different
epoch, so keeper/ledger/scratch work from the old epoch must be discarded.

Its ambiguity test includes the previous descriptor's entire position
uncertainty: `max_motion + previous_descriptor_bytes < ring`. Consequently
a repeated long-node index after approximately 717 us can already be
ambiguous, even though less than 819.2 us elapsed. FIFO/burst reserves and
the full first-to-last capture window also enter that bound. A broad initial
RX/TX capture cannot choose a previous-lap TX origin merely by modulo;
its possible next-lap interpretation is refused. Frequent same-node polls
also accumulate a bounded-clock stall deadline rather than hiding a stall.

The exported object carries actual descriptor `rx_lo/rx_hi/tx_lo/tx_hi`,
prefetch-adjusted TX upper bound, startup/overwrite source floor, protected
long observer-node beginning, first/last timestamps, generation, epoch and
an explicit `valid` flag. Descriptor, clock, generation, timestamp, jump,
stall or lap faults latch the whole pair unusable; a partially successful
RX update is never committed when TX fails. The adapter requires externally
proved clock/geometry/FIFO limits and running ownership. Those booleans are
not evidence that the hardware proofs have happened.

`test/test_dma_progress.c` passed108000 independent continuous-position
captures at nominal 40 MS/s, rotated RX/TX discovery and 16/24-KiB modeled
lag with both8-long/short-tail and IDF7-long/two-short RX at4/32-byte alignment
geometry, plus explicit tail-wrap, startup freshness, skew, hidden lap, stall,
unknown-capacity, invalid address, generation and overflow cases. This
validates the arithmetic/state API, not physical lag or actual prefetched
bytes. The production `T` command now calls a lease-held read-only BF0 reader
with first-read timestamp and actual transport generation; all startup and
rollback attempts invalidate that generation at entry. Its clock/FIFO proof
flags remain explicitly false, so it cannot authorize writes. The first board
capture refused the former hard-coded RX geometry; diagnostic output now names
the first metadata, shape or link mismatch with actual offset/length/address.
Host tests extract that actual production function and verify no raw, descriptor
or register writes, logging after release, and balanced PHY leases. Continuous
capture, verified runtime clock/memory bounds, `dma_progress_update()` and
`sync_stream_admit()` before each bounded commit are still required.

At a sampled instant, let:

- `Rlo` be RX's validated completed boundary;
- `Rhi` be the end of its current descriptor, an upper bound on its write;
- `Thi` be the end of TX's current descriptor plus proven prefetch;
- `C` be the beginning of the protected newest completed long observer node;
- `delta` be a conservative advance for the entire admitted CPU section.

Use the timestamp of the **first** RX/TX register read. Individual registers
are read sequentially; using a timestamp after the final read would hide
movement between those observations. Include elapsed acquisition/lease time
in the snapshot age. Establish the lease before selecting source/frontiers,
and revalidate generation under it before the final admitted commit.

An admitted write [a,b) must satisfy `a >= Thi + delta`, `b <= C`, and
`a >= Rhi + delta + rx_backlog - ring_bytes`; its detection inputs must be original,
complete and not overwritten. Include cache-line maintenance overlap in
these bounds. A preflight check is needed **before** stores: discovering
afterward that TX crossed a partial write cannot undo transmitted corruption.
Whole-pulse refusal is preferable to a truncated sync. Deadline/refusal
counters are part of monitor acceptance, not a replacement for it.

`main/sync_stream_guard.h` expresses this admission as a pure host helper:
validated frontiers/context/prefetch, complete original evidence, exclusive
raw ownership, cache-line rounding, elapsed-section budget and producer/
consumer bounds. `test/test_sync_stream_guard.c` passes adversarial tail,
lap, observer, stale-source and uint64 multiplication-overflow cases. The
caller must still supply **proved** inputs; a configured number is not WCET.

The guard now explicitly requires a proved RX burst/backlog bound as well as
TX prefetch. A queued RX FIFO can drain into RAM faster than the 40-MS/s input
cadence, particularly across the 32-byte tail. `rate * elapsed` alone misses
that advance. Its `rx_backlog_bytes` reserve must cover pending/in-flight
bytes beyond the descriptor bound, and `rx_burst_proven=false` refuses writes.
The synthetic 128/512-byte test reserves are not claimed hardware capacities.
Added 60000 adversarial fixtures independently recompute accepted cache-line
and source lifetimes with 128-bit arithmetic; all 832 accepted intervals
obeyed the worst-position constraints. Near-uint64 overflow is refused.

Prefer a two-stage whole-pulse commit: synthesize into an owned <=1280-byte
scratch area outside the critical section, then acquire exclusive raw
ownership, take fresh hardware frontiers, revalidate context/source lifetime
and admit the entire final copy/cache operation. Record provenance before
the first store. Seed/end-phase snapshots remain valid only if their original
absolute samples have not been overwritten or changed by another owner.
This avoids a long phase-generation loop modifying DMA-visible bytes while
preemptible. It requires a separate emitter/planner API; pointing the existing
ring function at a small scratch buffer does not preserve its indexing contract.

Before a partial scratch copy, refresh/invalidate the destination's complete
rounded cache lines from current RX memory, then perform the copy and flush.
Flushing a dirty cache line with neighbors retained from a previous ring lap
can otherwise overwrite genuine adjacent IQ that the patch/ledger never
intended to modify. Both M2C refresh and C2M completion belong in the admitted
time/lifetime interval. The actual C5 SRAM/cache policy must be verified;
passing interval arithmetic does not establish cache coherence.

Neither scheduler suspension nor a post-copy pointer check prevents interrupt
delays from exposing a half-written pulse to TX. A genuinely short interrupt-
masked commit can bound that risk, but its memcpy/cache WCET and impact on
other C5 interrupts must first be measured and justified. Keep expensive
synthesis, searching and printing outside it. If such a short bounded commit
cannot be established, use a proven hardware source-selection design rather
than declaring a preemptible shared-ring writer safe. A later failure report
cannot retract already transmitted malformed sync.

With a 4092-byte protected node, a worst descriptor-quantized nominal window
is roughly `16384 - 4092 - 4092 - 512 = 7688` bytes, only 192.2 us. Tail and
real separation move that window. Reserving 8190 contiguous bytes reduces
it to roughly3590bytes,89.75us. Independently, the actual two short RX
nodes mean protecting the newest completed4092-byte observer can cover8216
bytes when the active RX node is the first long node after both short nodes;
using4092 unconditionally would expose an older observer copy. The progress
adapter exports the whole preceding-short-nodes-plus-long interval. The host already exposed missing repairs
with that larger reservation. Treat optional black-raster watching and the
keeper as mutually exclusive until a different geometry is actually proved.
Do not silently reduce snapshot size or feed repaired IQ into its observer.

## Keep every existing raw observer honest

Control, gain probes and the overload sentinel choose the newest completed
long descriptor, skipping the short tail. Reserving that node protects new
copies, but a preempted copy can still be reading an older selected node
after RX advances. Implement one short shared raw-access ownership guard:
copies acquire a read lease before choosing/copying their source, release it
on every exit, and keeper writes require exclusive ownership without waiting
past their deadline. A keeper can skip a busy turn; it cannot race a copy.
An equivalent source-lease range is possible but more complicated.

Keep existing copy-overrun validation. Never hold this lease while running
gain/CN/AFC measurement; only source selection, cache synchronization and
copy belong inside it. The control copy already suspends the scheduler;
the probe and sentinel copies currently do not. Relying on task priorities
alone would leave a source-provenance race. Diagnostic full-ring/endpoints
captures must acquire the same lease or explicitly pause keeper writes
during capture. RX still runs, so their existing freshness bounds remain.
An 8190-byte snapshot can already include earlier repaired bytes: excluding
**new** writes during the copy does not make that older taint original.
Check its full absolute source interval against the ledger and refuse it
when tainted, or acquire it before the keeper reaches its source age. Keep
its 8190-byte contract; never silently substitute a shorter IQ capture.

The repair ledger expires only below a separately proved RX-overwrite
frontier. Read provenance covers complete phase/edge/width/level footprints.
Earlier patched bytes cannot refresh lock, field anchors, clock fit, AFC,
gain learning or a return-camera candidate.

## CPU budget is a real design gate

At 240 MHz there are only 15360 cycles per 64-us PAL line and about 15253
per NTSC line. `research/sync_stream_cost.py` cross-compiles the current
candidate with the pinned IDF toolchain at -O2, and records its disassembly.
The RV32 `lowm` loop contains 13-14 instructions per one adjacent difference,
repeated nine times, plus roughly 25 setup/exit instructions. This excludes
caller, provenance, memory stalls and scheduling. Instructions are not a
measured cycle count. The weak-signal evaluation totals also include some
32-step qualification bookkeeping, so multiplying the reported total by
one lowm cost would be inaccurate. Nonetheless it makes an important point:
many full pulse searches per line can consume most of the whole CPU before
repair, observers and native AGC.

`research/sync_lowm_cost.py` independently tests a small exact arithmetic
optimization: `((d + 128) & 255) - 128` plus GCC unrolling of the fixed nine
steps. For bounded phase differences [-255,255], its pinned RV32 emission
uses 112 instructions instead of approximately 144-153 for the original
loop. All 32768 random/wrapping host probes and every possible difference
matched across baseline, branchless, unrolled and combined versions. This
saves roughly 22-27% of this primitive's instructions without a large new
lookup table. Full candidate gates and C5 elapsed timing are still required.

Repairing a 188-byte H plus 48-byte front porch at every PAL line writes
3,687,500 synthetic IQ bytes/s. Exact DDA removes per-byte division but does
not make that free. Do not enable the current implementation merely because
host correctness passes. Capture real cycle/time maxima for detection,
prewarmed synthesis, cache synchronization and an entire task turn under
USB/native/gain load. Also measure cumulative CPU duty and missed repairs.

The simplest optimizations preserve the streaming contract:

1. Prewarm cell/template data outside the repair deadline. The current
   first `build_cells()` performs a 256-by-256 search and uses a 1-KiB local
   array; it cannot unexpectedly run on the first lost pulse.
2. In established repair/holdover, coast known timing and avoid re-running
   the full damaged-pulse search for every H. Sample original return-camera
   evidence on a bounded cadence, then qualify recovery. Do not count own
   template as evidence. For off-phase reacquisition, a sparse coarse search
   plus confirmed width/stability is cheaper than every-third-sample lowm
   throughout a whole line. Show it cannot miss true 4.7-us syncs.
3. Test prewarmed raw-IQ templates copied for admitted H intervals. A fixed
   phase template has 1.2 us of front porch to settle phase/LP before sync;
   the all-level carrier study demonstrates possible phase convergence but
   does not yet prove every actual transition. Preserve real back porch and
   burst, and require exact active-DAC equality in the host tests. This
   avoids millions of per-byte phase calculations without modifying DSP.
4. The first vertical equalizing pulse has no expendable front porch. Its
   local phase/LP bootstrap or template selection needs separate proof.
   This happens 50/60 times/s, so modest bounded local LUT work is reasonable.
   Broad/remaining V slots can use prewarmed patterns with exact whole-slot
   geometry. Do not erase picture to create a convenient bootstrap interval.

A 1-ms `vTaskDelay(1)` in the keeper is incompatible with the present write
window. A short blocking periodic notification near descriptor cadence can
leave IDLE time between bounded task turns; taskYIELD alone cannot. This
needs measured scheduler jitter. Native acquisition can suspend scheduling
for about 60 us plus interrupts, further shrinking a turn's reserve. Every
keeper turn must recheck real frontiers and abort before an unsafe store;
a higher priority alone does not solve physical deadline uncertainty.

## Cold start and every context change

The current flywheel can acquire only a real source. With no VTX at boot,
it cannot invent a real V anchor and therefore cannot already guarantee a
PAL/NTSC train. Add an explicit fallback timebase using the selected/last
genuine standard if no genuine source was ever acquired. This generates
only missing sync/blanking; active bytes still come from fresh RX noise.
Do not label that state camera lock. Clean received sync must remain
unmodified while being acquired. The fallback policy therefore needs an
explicit bounded absence test and separate return-camera detection.
Reuse the already existing `menu_standard()` selection: forced PAL/NTSC,
otherwise a stable detected standard, otherwise the existing saved last
standard/default NTSC. No extra NVS key or new menu is needed merely to
choose a never-acquired fallback. Check its mode/generation through each turn.

Camera return can supply credible H within a few lines while its next genuine
vertical train is still almost 20 ms away. Keep camera-qualified H and V
evidence separate. Do not move a generated vertical train to a guessed new
camera field phase, shorten a half-line, or call a horizontal period vote a
proved field anchor. A returned camera at another standard requires genuine
new-standard geometry and parity before handover. Until that is known,
retaining old V timing can overwrite new-camera active content; disabling
that uncertain V replacement can briefly reduce the LCD's field guarantee.
This is a real unresolved acceptance tradeoff, not something a lock Boolean
can remove. Test the exact return-phase/standard transition and choose the
smallest bounded behavior that preserves received information and LCD lock.

A transport restart invalidates old absolute samples, ledger, tracker/LP
state and descriptor timing. kp, LP and output scaling are applied only
through the existing verified halted restart/rollback/cooldown. A keeper
must operate from verified actual settings, never a pending target. Tag
every turn/template with that transport generation and actual kp/LP/output;
refuse mismatched or unknown contexts. RF retune, scan, PHY ownership and
menu/status TX ownership suspend writes; re-establish fresh RX/TX data
before returning. Gain/native pulses also invalidate measurements that span
their change; holdover timing can remain if the same camera/channel is still
the context, but the disturbed raw interval cannot qualify recovery.

Use the stable-frequency study for all eight levels and all nine output
settings. At low kp the old -6-MHz carrier fails, and exact DAC zero is not
always reachable. A template must target verified output behavior; choosing
another frequency is preferable to an unproved live decoder rewrite.

## First buildable delivery

Implement transport generation/frontier extraction and the pure interval
admission guard first, with adversarial host tests for tail, wrap, gaps,
prefetch, elapsed budget and unknown context. Wire one prewarmed keeper task
into the firmware source list, with observer leases and monotonic provenance.
Make its initial physical acceptance mode explicit and RAM-only; keep the
default repair policy off until full correctness and timing gates pass.
This is an engineering staging step, not the user's final automatic feature.

Test on the affected monitor using real gradual attenuation, no-VTX boot,
loss beyond 2.5 s, changed-phase/standard return, native pulses, USB load and
kp/LP/output transitions. Require valid terminated CVBS, fresh received snow,
no clean-signal edits, bounded recovery, zero partial-write races and measured
CPU/deadline headroom. Then promote the necessary-only keeper to automatic
behavior and close the monitor issue. A passing host raster alone does not
complete that task.
