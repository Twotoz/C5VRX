# Read-only continuous camera detector: bounded next experiment

This is C5VRX by Twotoz and the C5VRX contributors, adapting the current
`main/sync_flywheel.c` detector and transport observations. The canonical
[source](https://github.com/Twotoz/C5VRX) and
[website](https://twotoz.github.io/C5VRX/) provide project provenance; the
Discord invite is on the website. No production kernel or transport change
was made by the acquisition experiment. The manual W diagnostic described
below is now implemented; a continuous hardware detector is not yet proven.

`W` toggles the new read-only service; `T` prints `SYNC_SERVICE`. It is OFF
at boot. `main/sync_service.c` lazily allocates its FSM/phase/context and
4096-byte priority5 task, prewarms before arming a1MHz/80us GPTimer, and blocks
on ISR notifications between turns. Stop, coalescing, source failure or an
80us service overrun stops/deletes the timer and task and frees the context.
There is no persistent diagnostic task or FSM after OFF. W excludes J while
its shared kernel state is in use. This first profile uses unchanged
ACQUIRE with192evaluations, so its known backlog is reported rather than
hidden by the coarse research candidate.

`transport_sync_source_begin/end` bracket original-ring reads with a raw
READ token, metadata timestamps, DONE/BF0, immutable discovered layout,
context/clock and fault checks. The256-byte phase table is in task context
RAM; the actual live LUT pointer is used under that context without copying
it or claiming program verification. `allow_repair=false` is unconditional.
The post-read check discards camera evidence on invalid context or timing.
An80us prospective AHB-motion reserve advances the original-source ledger's
oldest readable boundary; physical publication remains explicitly unproven.

Status includes capture span/gap, last DONE result, total and separate
ACQUIRE/TRACK cycle maxima, work counts, acquisition backlog, rejections,
coalescing and stack high-water. Stopped results never report a live lock.
There is no console output inside the service loop. User-facing output and
native RF controls are not connected to this detector.

`python3 test/test_sync_service_runtime.py` compiles/executes the actual
service ISR/task/turn and actual transport adapter with mocked SDK registers.
It checks notification-only ISR behavior, stop/free/restart, overrun and
source rejection, balanced leases and raw/register immutability. It does
not supply physical timing evidence. Hardware build/run remains the next
step after review.

## Completion coordinates and their limits

The primary ESP32-C5 Technical Reference Manual v1.1, section 5.4.1, p187,
defines RX descriptor length as valid bytes hardware has stored in the
buffer. Register 5.35, p225, defines BF0 as the current **pre-read** RX
descriptor; register 5.33 describes its next descriptor as not processed yet.
Registers 5.51/5.52, p235, define DONE as the descriptor corresponding to a
completed RX/TX transfer. See the [official TRM](https://www.espressif.com/sites/default/files/documentation/esp32-c5_technical_reference_manual_en.pdf).
BF0 minus one is consequently not a demonstrated completed-payload frontier.
DONE is a useful diagnostic completion coordinate; this text does not claim
a CPU/DMA visibility barrier, maximum publication lag, or permission to write.

`research/sync_done_progress.h` is a reference observer for that narrower
diagnostic. It accepts two context/clock/timestamp/DONE captures; BF0 is
recorded independently and never supplies completion. A crossed DONE boundary
is retryable. Other invalid captures latch failure until explicit restart and
discard of private detector state. The 32768-byte layout is discovered and
validated independently, including actual RX 7*4092+2062+2062 and historical
8*4092+32 geometries and rotations. Completed offsets include the true tail.

With the conservative 192MB/s AHB ceiling, capture-to-capture motion plus one
maximum 4092-byte partially transferred descriptor must be below a ring. This
permits at most149us integer timestamp separation. The observer checks the
whole interval from the previous first capture through the current last
capture, including retry time. It rejects a possible hidden lap; it never
infers continuity from equal addresses alone. Its output always has
`memory_write_permission=false`. The caller still needs a raw-reader lease,
source freshness and a read-duration bound; DONE does not replace those gates.

Reproduce the independent completion-coordinate regression:

```sh
cd test
gcc -O2 -std=c11 -Wall -Wextra -Werror -I../main -I../research test_sync_done_progress.c -o /tmp/test_sync_done_progress
/tmp/test_sync_done_progress
```

360000 accepted/retry trace observations span both geometries/all nine
rotations. Payload position independently determines DONE while BF0 moves
two or three descriptors ahead. Explicit tests cover the32-byte wrap,150us
ambiguity, bad clocks/context/address/time and restart epoch. These are host
coordinate tests, not evidence of physical publication ordering.

## Acquisition throughput experiment

Run `python research/sync_acquire_stride.py` with Docker/gcc:14 and the normal
research Python dependencies. It snapshots/hash-identifies the actual core,
builds that core and a temporary ACQUIRE-only variant, and replays fresh
original IQ into a private32768-byte ring every80us/3200samples. It calls
`sfw_run(... allow_repair=false)` and byte-compares the entire ring after every
call. No oracle timing is passed to the detector. The report is
`research/data/sync_acquire_stride.json`; its core hash identifies the measured
revision, so subsequent kernel changes require an explicit new experiment.

The original histogram takes6800votes at stride3. At192evaluations/80us it
falls behind input and repeatedly discards stale acquisition positions. A
temporary candidate takes the same6800votes at stride63, then seeks at63 only
while high. A low hit backs up63 and refines at3; low-run width150..230 and
PAL/NTSC period checks remain the actual core's existing checks. Stride63 is
coprime with PAL2560 and the rational NTSC line numerator22880, avoiding the
fixed lattice lock that stride64 would have for PAL. Coprimality alone does
not establish statistical independence on arbitrary RF; the unchanged vote
count and longer temporal coverage are the conservative first experiment.

An independent lattice check covers all81 widths150..230 and all63 offsets
(5103cases). After allowing nine samples for the averaging support, the
minimum plateau cannot fall between coarse probes; backfill locates its
first low within two samples. This geometric guarantee assumes a contiguous
low plateau, and does not claim noisy RF will provide one.

| Input (three fields) | Original1200 | Original192 | Coarse192 |
| --- | --- | --- | --- |
| Existing C fixture PAL | lock3.60ms | fails;185stale jumps | lock13.76ms; maxACQUIRE120 |
| Physical ADC simulation PAL | lock3.60ms | fails;185stale jumps | lock13.76ms; maxACQUIRE128 |
| Existing C fixture NTSC | lock3.60ms | fails;154stale jumps | lock13.76ms; maxACQUIRE117 |
| Physical ADC simulation NTSC | lock3.60ms | fails;154stale jumps | lock13.76ms; maxACQUIRE125 |

The two generators are independent; the physical generator uses corrected
PAL/NTSC source geometry. Seed/config/input hashes are in the artifact.
Uniform byte noise produces no lock; this is false-positive stress rather
than an RF noise model. All ten8190-byte real snapshots are explicitly too
short to establish the fresh initial ring epoch: zero detector calls, zero
acceptance evidence. Histogram latency increases to about10.7ms; measured
first lock includes subsequent period confirmation. Raw IQ stays byte-exact.

This does **not** make192 a hard per-turn budget: existing TRACK/V pulse
classification reaches484..499evaluations even in the coarse experiment.
The original1200-budget PAL fixture reaches847 in TRACK. Work counters are
not C5 cycles. No continuous implementation should be enabled on those
numbers alone. Next kernel work must suspend/resume pulse search and width
classification, carrying original-source context and cursor, without changing
the averaging, width, period or camera-lock qualifiers.

## Live implementation contract and remaining measurements

1. Factor the read-only metadata bracket in `transport_sync_probe_print` into
   a reusable capture. Discover/copy immutable descriptor geometry once per
   stream epoch outside the hot path. Keep console formatting out of service.
   Capture timestamps around context, RX/TX clock, BF0 and DONE reads; use
   the existing metadata/raw ownership protocol rather than pausing the PHY.
2. Prewarm shared synthesis cells before arming service (a zero-budget,
   zero-available `sfw_run` does no raw reads). The private bench prewarm's
   private ring is not the live ring. One kernel owner must serialize the
   detector with J/bench because current core evaluation/cell state is global.
   Detector-only ring state needs no decoder LUT copy or program verification.
3. A1MHz GPTimer with80us auto-reload invokes an IRAM ISR that only calls
   `vTaskNotifyGiveFromISR` and returns its wake flag. A diagnostic task blocks
   on `ulTaskNotifyTake(pdTRUE, portMAX_DELAY)`. No1ms polling delay, busy loop,
   console output or catch-up loop. A coalesced notification count>1 breaks the
   timing epoch; rejected capture or expired source context discards the
   private detector. Restart only on a newly validated epoch. Release all
   raw leases before blocking. Timer notifications change no source clocks
   or EOF configuration.
4. Initially wait one full newly completed ring after bootstrap; old contents
   before the initial DONE observation have no proven source origin. Then
   acquire a bounded original-IQ read window, run only the measured work slice,
   capture the end context/frontier and reject the result if its source lease
   was not valid throughout. No synthetic source, original-IQ write, descriptor
   write, PHY write or active output path participates. This detector reports
   real camera acquisition/standard/phase independently of a generated clock.
5. Export counters only on a separate slow console request: notification gaps,
   capture span/spacing, rejection reason, raw lease duration, ACQUIRE/TRACK
   cycles separately, maximum/percentile task time, stack high-water mark,
   real H/V acquisition, cursor lag and IDLE runtime. Require measured task
   WCET below80us with margin and every accepted metadata interval below the
   conservative149us bound. Demonstrate camera loss/return and context breaks
   on real raw data before connecting this state to any writer.

Pinned RISC-V O2 compilation gives `sync_flywheel_t`1928bytes, observer80bytes,
capture56bytes and layout144bytes. Start with a static detector state and
4KiB task stack, no full-ring mirror; validate high-water on hardware. Static
compiler stack reports (`sfw_run`192, `find_pulse`96, `build_cells`1056) are
planning data, not a dynamic maximum-stack proof. Start priority5 as an
experiment and measure scheduling/IDLE impact; priority is not a timing proof.
This prepares/reuses cells and coasts the PLL instead of decoding every
40MS/s sample on the CPU. The manual diagnostic supplies the measurement
mechanism; enabling an automatic detector or writer still requires the above
remaining bounded-work and physical service measurements.
