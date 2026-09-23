# Phase5+ two-bundle demodulator

## Full-turn requirement and current feasibility boundary

At 40 MS/s, an adjacent phase step is identifiable only when its magnitude is
strictly below 180 degrees. Two such 25 ns steps can add to more than 180 degrees
over 50 ns (for example 0 -> 110 -> 220 degrees); their signed sum preserves the
turn that a 50 ns endpoint difference loses. No demodulator can infer a single
25 ns step beyond 180 degrees from sampled IQ alone without a motion prior.

The full-current-raw8 recurrent LUT16 proposal has only two addressed history
bits; the LUT8 version can address three. `tools/prove_polarcell_capacity.py`
shows that exact adjacent Phase5 delta over bounded 25 ns steps up to 135
degrees requires distinguishing all 32 previous phase bins (five bits). Both
proposals can be trained approximations but cannot guarantee full-turn recovery.

`tools/evaluate_winding_hints.py` exhausts a 32-bin smooth-motion model with
each adjacent step at most 135 degrees and adjacent-step difference at most
67.5 degrees. With exact five-bit endpoint phases, one middle half-plane bit
permits unambiguous correction of 1,572/2,400 winding events; two middle
quadrant bits permit 2,400/2,400. However the same tables miscorrect 1,900
and 1,016 respectively of all 32^3 arbitrary phase triplets. These are not
RF error rates: they demonstrate that a motion-model gate alone cannot promise
clean video when IQ is noisy or discontinuous. A live design must preserve
Golden on uncertain cells and be compared on recorded/live IQ.

A possible Golden-preserving TX-only one-bit pipeline delays each DAC write one
pair. Conceptually, bundle A addresses the normal five-bit endpoint-pair LUT
and branches on a raw middle sign bit; bundle B stores the Golden DAC and a
rail-polarity bit, addresses the next raw endpoint, and branches on the
corresponding LUT correction flag. The next A emits either the saved Golden DAC
or the selected rail. A 16-bit LUT word has room for Golden DAC6 + phase5 plus
two correction flags and one polarity bit. This would consume every raw Q4/I4
sample and leave RX BitScrambler unused. **The required four-way branch cycle
has not been assembled or shown to loop in eight instruction slots without an
extra jump.** Therefore this is not yet an executable two-bundle program. Its
one-bit hint also cannot recover every full-turn trajectory, and the
smooth-motion gate has false corrections on arbitrary phase triplets.

The desired combination of exact Golden behavior on arbitrary noisy IQ, exact
full-turn recovery on all admissible smooth trajectories, all 40 MS/s raw IQ,
TX-only processing, and two bundles per 50 ns has **not** been demonstrated.
Do not treat an assembler probe or synthetic score as proof of static-free
video. The immediate experimental target is a Golden-preserving correction
whose default branch is byte-identical to Golden, followed by live A/B and an
IQ capture to measure whether >180-degree 50 ns events actually occur.

### Review of the proposed PolarSigma P6+M4 -> token4 -> C6 design

Its algebra for unwrapped adjacent phase differences is valid. The proposed
two-bundle LUT routing may also be schedulable in principle. Neither point
establishes exact adjacent FM or clean video:

- The demodulator actually uses 6+4+6 of the 24 raw bits in `(p,m,c)`, so it
  omits two previous, four middle, and two current IQ bits. Keeping all bytes
  in the RX DMA ring does not make those omitted bits available to the LUT.
- `tools/prove_polarsigma_token.py` enumerates all 32^3 Phase5 triplets using
  the production P20/G2 DAC calibration. The `(p,m)` histories require 224
  distinct continuation classes for exact 25 ns adjacent DAC pairs (eight
  token bits). Even the repeated-DAC unwrapped 50 ns sum requires 1,024
  classes (ten bits). A four-bit token has only 16 values. This does not rule
  out a useful learned approximation on real RF, but exact full-turn behavior
  is impossible for this fixed stage split.
- `(Y0+Y1)/2 = P+KS` holds before DAC clipping/rounding and only if `S` is
  correctly estimated. For `d0=+45 deg, d1=-45 deg`, `P=20`, and current
  P20/G2 scaling, ideal adjacent codes 68 and -28 clip to 63 and 0. Their
  mean is 31.5, not 20. If the estimated winding in `S` is wrong, setting
  alpha to zero leaves the same wrong level in both DAC bytes; that error is
  not shifted to 20 MHz.
- With the current gain, an unwrapped +220-degree 50 ns difference maps above
  code 63 and saturates. Preserving the winding internally is still useful
  for polarity, but an unchanged 6-bit DAC transfer curve cannot represent
  the full +220-degree magnitude linearly.

## Hardware disposition: blocked by C5 half-duplex BitScrambler

The implementation below is a valid host-side DSP/LUT experiment but **cannot
run as a live ESP32-C5 receiver**. The [ESP32-C5 datasheet, section 4.2.1.12](https://documentation.espressif.com/esp32-c5_datasheet_en.html)
states that its BitScrambler RX and TX channels support only half-duplex
operation and cannot work simultaneously. The [technical reference manual,
BitScrambler chapter](https://documentation.espressif.com/esp32-c5_technical_reference_manual_en.pdf)
states the same restriction. Phase5+ requires RX to predecode IQ while TX
demodulates the same live stream, so its hardware topology violates this limit.

Live A/B on the ESP32-C5 v1.0 board, A1/5865 MHz, BW40, manual gain G62:

| Firmware | RX DMA window | Observed output |
| --- | --- | --- |
| Golden TX, RX BitScrambler disabled | 32 low-five-bit values, mixed raw IQ bytes | Video with VTX on |
| Golden TX, RX BitScrambler identity passthrough enabled | 4,092/4,092 bytes `0xFF` | Black screen |
| Same identity passthrough with hardware prefetch and RX started before BitScrambler | 4,092/4,092 bytes `0xFF`; RX BS `ctrl=0x000000c1`, `state=0x00030002` (`in_run=1`) | VTX off; video not evaluated |

The RX GDMA pointer still advanced and transport fault counters were zero.
The identity program removes all phase math and LUT quantization from the
test, so the static is upstream of Phase5+ arithmetic. Enabling prefetch and
changing start order did not restore data. The observed `0xFF` pattern by
itself does not identify which internal mux or FIFO supplies those bytes; the
documented half-duplex limit is the decisive reason to retire this topology.

Do not revive an RX-BitScrambler plus TX-BitScrambler variant, including a
one-bit winding preprocessor. A successor must retain raw IQ capture and use
only the TX BitScrambler in the realtime path, or use a separately proven
accelerator that can run concurrently with TX. A host oracle and two TX
bundles alone are insufficient evidence of a runnable C5 design.

### TX-only successor candidate (not hardware validated)

Keep Golden's raw 8-bit Q4/I4 DMA ring and consume both bytes of each 50 ns
pair. The TX BitScrambler can use the middle byte's Q sign as a one-bit
winding hint while retaining a five-bit state for each endpoint:

- Encode an endpoint as two raw I/Q sign bits and three phase-within-quadrant
  bits from a 256-entry LUT. This is 32 states, though the nine Golden phase
  values observed in each sign quadrant must be remapped to eight; at least
  one boundary bin per quadrant cannot be preserved byte-for-byte.
- Use the previous and current five-bit states as the 10-bit address of the
  same 1024x16 LUT. Each word carries two six-bit DAC outcomes: one for each
  value of the middle Q-sign bit. Its remaining upper three bits supply the
  phase-within-quadrant code when the word is addressed by a raw IQ byte.
  The first 256 words serve both purposes without exceeding 16 bits
  (`6 + 6 + 3 = 15`).
- In the first steady-state bundle, address the endpoint-pair LUT and branch
  on the raw middle Q sign. In the second bundle, emit the selected DAC code
  twice and address the next endpoint's raw-to-phase LUT entry. Both branch
  targets jump back to the first bundle. This is four stored instructions
  including the startup prime, but exactly two executed bundles per 50 ns.

An ESP-IDF v6.0.2 assembler probe accepted the complete four-instruction
schedule, including mixed LUT/output/input bit sources and the middle-sign
branch. This proves encoding feasibility, not sustained hardware throughput.
The CPU keeps receiving untouched raw IQ for ARC and diagnostics.

This is a conservative *approximation*, not exact adjacent FM. Under a simple
32-bin model with both adjacent steps at most 12 bins and their difference at
most 6 bins, the middle Q sign uniquely identifies the winding branch for
1,572 of 2,400 winding triplets (65.5%). Ambiguous endpoint/sign cells must
fall back to Golden. These model counts do not predict live image quality:
quantized IQ, noise, and trajectories outside the gate can change the result.
The required next proof is a raw-IQ oracle and a live Golden A/B with CVBS lock,
image quality, transport counters, and exact DAC timing. Do not enable this
candidate as a default based on assembler or model results alone.

### Full adjacent FM path (CPU feasibility unproven)

The entire three-phase result does **not** need a 32,768-entry direct LUT.
For `D = clip(20 + 6 * (wrap32(m-p) + wrap32(c-m)), 0, 63)`, exhaustive
factorization with the existing Phase5+ address split produces **24 distinct
interstage vectors**. Five token bits suffice, and all 32,768 phase triplets
reconstruct exactly through a single 1024x16 (2 KiB) LUT in two TX bundles
per 50 ns. This clips only the summed 50 ns FM result, unlike PR #69's two
separately clipped 25 ns outputs. It still assumes the phase of *every* raw
IQ sample is available before TX reads it.

With RX BitScrambler unavailable during TX, the only identified single-chip
preprocessor for the proven TX datapath is the 240 MHz CPU: map all 40 MS/s raw IQ bytes through a
256-byte raw-to-Phase5 table, writing phase symbols into completed RX DMA
blocks before TX reaches them. The CPU has only six cycles per input sample
(12 cycles per two-sample pair) at that rate. A 65,536-entry pair table with
16-bit outputs would occupy 128 KiB, while the current firmware's IDF size
report leaves about 52 KiB of HP SRAM before runtime allocations. The
256-byte table avoids that memory cost but has not met a measured end-to-end
cycle, DMA-coherency, and control-task budget. Raw IQ needed by ARC must be
observed or copied *before* in-place conversion. A missed conversion deadline
must fail safely back to Golden; it cannot silently mix raw and phase bytes.

The 257-bit instruction word is a wide routing/control word, not 257 ALU
operations or an additional LUT. Many `set` routes, one `read`, one `write`,
and one opcode can already run in one bundle. The C5 still supplies only one
LUT result per bundle, with the next dependent address available a cycle
later. In the straightforward raw-IQ formulation, each 50 ns pair needs two
new raw-to-phase lookups (middle and current endpoint) and two dependent
phase-domain factorization lookups; packing `set` routes cannot reduce those
four lookups to two. RMT provides timed pulse channels, not a
40 MS/s complex-IQ-to-phase lookup. If a measured CPU preprocessor cannot
sustain this rate, exact full-adjacent FM needs a separate proven parallel
preprocessor or a different SoC; the 2 KiB LUT alone cannot ingest raw IQ and
finish all three phase lookups in two bundles.

### Eleven-bit direct raw-pair LUT search

The 2 KiB LUT can alternatively be configured as 2048x8, giving an 11-bit
address. `tools/evaluate_raw_pair_projection.py` enumerates all 4,368 fixed
choices of 11 of the 16 bits in `(previous_raw, current_raw)`, and allows the
best possible exact Phase5 adjacent-delta answer per address. None is exact.
On the uniform 65,536-pair space, the best choice matches 23,032 pairs
(35.14%). Its angular-error mean is 10.9 degrees and its 95th percentile is
33.8 degrees. Every one of the eight raw bits affects the production Phase5
mapping for at least one input, so dropping five pair bits cannot preserve
all adjacent deltas. These numbers are a table-capacity bound, not a predicted
live-RF video score; real IQ pairs are not uniformly distributed.

The B counter's comparators compare B/BH/BL to selected slices of the
*previous* output word. They can carry state or make threshold decisions, but
do not provide another LUT lookup or reconstruct the raw bits omitted from
an 11-bit address. Loading/adding counters also uses the sole opcode slot,
which must accommodate loop control. A more elaborate multi-bundle counter
pipeline remains possible in principle; no exact two-bundle raw-IQ program
has been demonstrated.

An RX-only BitScrambler followed by undecorated PARLIO TX avoids the observed
RX+TX concurrency failure. Its sustained RX cadence still needs a live
identity test. Even if one bundle per 25 ns works, the straightforward exact
raw-IQ solution requires four LUT accesses per 50 ns pair (two raw-to-phase,
two phase-domain), exceeding the same two-bundle compute budget. This route
therefore needs a new mathematical factorization, not merely rewiring the
existing TX program to RX.

## Host-side experiment

Phase5+ fixes a specific error in the 25 ns Polar11 experiment: clipping two
adjacent FM differences separately makes a temporary middle-sample phase error
survive as video. A 0° → 50° → 0° excursion produces 63 and 0 in Polar11,
whose average is 31.5 instead of the black pedestal 20.

The proposed RX BitScrambler converts each 40 MS/s raw Q4/I4 byte to the
production five-bit phase plus a three-bit envelope class. The TX BitScrambler reads three
successive phases `p,m,c`, decides whether the middle sample proves that the
endpoint difference crossed ±180°, then maps the *single* 50 ns result to the
DAC. Its 20 MS/s output is held as `[D,D]` at the physical 40 MHz DAC clock.

```
e = wrap32(c - p)
d0 = wrap32(m - p)
d1 = wrap32(c - m)

if abs(d0) <= 12 and abs(d1) <= 12 and
   abs(d0-d1) <= 6 and abs(d0+d1) >= 16:
    delta = d0+d1
else:
    delta = e

DAC = P20/G2(delta), clipped once
```

For incoherent endpoints near half a turn, the LUT approximates Golden's
pedestal suppression rather than sending them to a DAC rail. The 32³-entry
oracle factors into a 1024×16 LUT queried twice per 50 ns pair. Its first
lookup produces one of 30 five-bit tokens; its second produces the DAC value.
The TX steady state is exactly two BitScrambler bundles per 50 ns. The proposed
RX program runs at one bundle per 25 ns and keeps every sample, though it
compresses raw Q4/I4 amplitude and does not preserve all eight original IQ bits.

The host tests exhaust all 32,768 phase triplets and execute the actual TX
instruction bundles against 2,046 random pairs. They do not prove a live
pipeline; the hardware A/B above disproves this RX+TX topology. ARC remains
manual in this mode because the existing gain controller expects raw Q4/I4
bytes, not phase/envelope codes.
The serial `P` command reports the Phase5+ ring's number of distinct phase
states, envelope-class histogram, and first four bytes without changing gain
or the video datapath. `J` selects Phase5+ or Golden and reboots because the
ring formats differ.
