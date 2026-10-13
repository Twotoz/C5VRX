# Sync synthesis across the real kp ladder

C5VRX by Twotoz and the C5VRX contributors. This repository research uses
the root shared-word LUTs, Phase8 cell table and exact U85Q-LP arithmetic.
`python research/sync_lut_feasibility.py` regenerates
`research/data/sync_lut_feasibility.json`. No firmware integration or board
acceptance is claimed.

The previous assertion that -6 MHz always produces a known DAC-zero tip is
false across the range ladder. In the exact digital model, three initial
carrier phases, two pair parities and three initial tracker states produce:

| kp | LP output at -6 MHz | fraction above code 8 | stable deeper candidate |
| --- | --- | --- | --- |
| .85-.65 | 0-3 | 0 | -6 MHz |
| .60 | 4-30 | .80 | -5.5 MHz: 2-4 |
| .55 | 4-29 | .80 | -5 MHz: 4-5 |
| .50 | 10-34 | 1.00 | -4.5 MHz: 5-7 |

The frequency candidates use a margin from the circular tracker ambiguity
boundary. A single conservative -4.5 MHz frequency produced LP codes 4-8
at every kp, with no steady-state sample above the host sync threshold of 8.
This is the simplest digital baseline for testing all levels. A small table
choosing the deepest stable frequency for the verified active kp preserves
more sync amplitude; the table must be tested through every repair transition,
clock offset, endpoint-residual correction and output setting before use.
The nominal carrier sweep excludes its first 256 pairs and therefore does
**not** prove a 2.3-us equalizing pulse starts promptly.

Exhaustively checking all 1024 phase/token transitions finds a minimum raw
discriminator code of 2 at kp .55 and 4 at kp .50. Some .50 phase states cannot
produce below 7 in one transition. Exact DAC zero at 1x is therefore impossible
with the unmodified low-kp worker LUT, even with perfect tracker-state seeding.
Larger manual output mappings may clip those values to zero, but their porch
level also changes. Digital codes establish neither the loaded voltage nor
the affected LCD's clamp/AGC response. Measure terminated CVBS.

## Why a local state bootstrap needs an explicit failure path

For each observed input pair, updating all 32 possible initial phase states
through the true LUT supplies a proof of the current phase only if they
converge to one state. Tested -4.5 MHz carriers converged in 3-8 pairs across
the ladder. At .50, a 0-MHz constant porch and a -6 MHz carrier did not merge
all possible states within 2048 pairs. Therefore a fixed-length warm-up must
return "unknown" when candidates remain; it must never choose one arbitrary
phase and claim proof. Counter/LP state is separate from phase and must also
settle or be modeled. The real hardware pair alignment is part of the input
contract. Changing initial phase may improve an edge but cannot fix the
low-kp steady-state ambiguity above.

## Reserved-token shortcut is not transparent

At kp .50, all four low-radius origin tokens have exactly the same phase/DAC
worker transitions as regular phase tokens 3, 10, 17 and 24. At higher kp,
48-112 of those 128 transitions differ. Remapping them could free control
tokens losslessly at .50 **in the worker model only**. It is not a general
transparent marker scheme: every 4411 controller address is a physically
possible real IQ pair. A marker that forces sync/blanking also changes the
output when that same pair occurs naturally in active video or snow. A
separate explicit control plane would be necessary to distinguish the two.
Do not install a marker LUT permanently or switch live LUTs per pulse; the
production verified halted-switch/cooldown contract remains unchanged.

## Keep repairs out of evidence

`main/sync_repair_ledger.h` is an independent bounded provenance helper.
Record the whole admitted [begin,end) write **before** modifying the ring.
Only a verified absolute RX overwrite frontier may expire ranges. Ring
address wrap, a TX pointer and successful detection do not authorize expiry.
Queries reject overwritten, future, underflow and repaired reads. Sorted
intervals support a cheap newest-data path and O(log 64) overlap lookup.

The tested worst PAL/NTSC vertical-slot density over twelve fields retains
at most 21/22 ranges in one 32-KiB horizon. Repeated/overlapping/adjacent
repairs merge. The 64-range limit is not trusted as a safety assumption:
overflow collapses the entire tainted extent to one conservative range.
Unmodified gaps can then lose qualification, but synthesized bytes cannot
become "genuine" evidence. Expiry eventually clears this conservative taint.
The helper occupies approximately 1 KiB; it is not yet connected to the
flywheel and does not prove cache/DMA correctness.

Evidence qualification must cover the whole read footprint:

| Existing operation | Required unmodified sample interval |
| --- | --- |
| adjacent step at k | [k-1,k+1) |
| nine-step lowm at k | [k-5,k+5) |
| steady_tip(start), 32 steps | [start+59,start+92) |
| learn_levels sync, 128 steps | [start+29,start+158) |
| learn_levels front porch | [start-61,start-28) |
| learn_levels back porch | [start+191,start+208) |
| pulse-width/edge search | union of every lowm footprint actually examined |
| clean_pulse probes | every lowm footprint used, including confirmation probes |
| clock fit | only an edge whose complete detection/qualification was original |
| acquire histogram | adjacent-step footprints; skip taint and break run continuity |

Checking an edge centre alone is insufficient. In particular, the existing
previous-line reacquisition scan reads a region already repaired during an
earlier call. Its excluded prediction window does not establish provenance
for another phase, half-line vertical slot or an earlier repair. Tainted
reads cannot refresh the clean counter, clock, field anchor or reacquisition
candidate. Losing qualification must continue holdover rather than turn
the repair output itself into a recovered camera.

## Continuous-loss and hardware acceptance remain separate gates

The inherited 40000-line timeout assumes an idle raster replaces real video.
That assumption conflicts with preserving genuine snow indefinitely. Keep
the last valid PAL/NTSC field clock running while reacquiring from proved
unmodified samples in parallel. Do not discard field phase or blank the
screen solely because 2.5 s elapsed. Test losses beyond that boundary and
camera return at a different phase/standard. If no source standard was ever
observed, retain an explicit selected default; do not claim camera lock.

All 40-MS/s data remains hardware streamed. A CPU loop rewriting only sync
intervals can still exceed its budget: even one 188-byte H pulse per line is
about 2.94 million synthesized bytes/s, before front porches and vertical
pulses. Cheap exact DDA arithmetic is helpful but not a deadline proof.
Measure cold/prewarmed synthesis, sustained weakest-signal processing,
control/observer contention, USB load and cache maintenance on the C5.
Do not add a third BitScrambler bundle or use CPU pacing for active pixels.

RX freshness, TX prefetch and raw observer reservations must be checked
against physical descriptor frontiers for each whole admitted write. The
host 512-byte prefetch guard is an assumption. Refuse a late repair rather
than shorten a pulse, and count misses so the affected LCD test exposes them.
Only terminated CVBS and LCD tests through gradual attenuation, long loss
and recovery can substantiate continuous monitor sync.
