# Adjacent50 pair-LUT candidate

This is a host-verified design for the XIAO ESP32-C5 and the existing resistor
DAC. It is **not** a live flight mode yet. The remaining acceptance tests are
CPU throughput while RX/TX DMA is active, safe in-place ring ownership, and
video quality on real RF. Keep Golden as the default until those pass.

## Datapath

For each consecutive pair of full Q4/I4 bytes `(m_raw, c_raw)`, a 128 KiB
`65536 x 16` software table produces exactly:

```
low byte:  m[4:0], d1[2:0]
high byte: c[4:0], d1[4:3], spare
d1 = wrap32(c - m)
```

`m` and `c` use the exact production raw8-to-Phase5 mapping. No raw IQ bit is
discarded before that mapping; every current and middle raw sample is used.
The CPU operation is one aligned 16-bit read, one indexed table read, and one
aligned 16-bit write per 50 ns. The current compiler emits seven instructions
per pair for the in-place loop. This is an instruction count, **not** a
measured cycles-per-pair guarantee. The C5 offers twelve 240-MHz CPU cycles
per 50 ns, shared with all other firmware work.

The TX BitScrambler then runs exactly two bundles per pair:

```
bundle 1: previous endpoint p5 + middle m5 -> LUT high field d0_5
          emit prior DAC twice
bundle 2: d0_5 + supplied d1_5 -> LUT low field DAC6
          retain c5 as the next p5; read the next 16-bit pair
```

The same 1024-entry LUT16 word contains both independent functions. There is
one TX BitScrambler, no RX BitScrambler, no M2M pass, and no third bundle.
The output is `[D,D]` at the existing 40-MHz 6-bit DAC, preserving the
50-ns pair average and avoiding the standalone 25-ns noise increase.

`S = signed5(d0) + signed5(d1)` is never wrapped again. It spans -32 to +30
Phase5 bins, covering winding across the 50-ns endpoint boundary as long as
each 25-ns step is below the fundamental +/-180-degree alias limit. Exactly
180 degrees per step remains ambiguous by sampling, independent of hardware.

## Golden-compatible transfer

For non-winding `S`, the DAC uses the median of Golden's 32 phase-rotated
outputs for the same endpoint difference. Across all 24,576 no-winding
Phase5 triplets, 19,664 outputs are byte-exact Golden; the others differ by
at most two of 63 DAC codes (mean absolute error 0.209). For the 8,192
winding triplets the corrected output reaches the mathematically correct
rail, 0 or 63. The chosen P20/G2 gain already saturates well before a full
360-degree displacement; the win is avoiding a *wrong-polarity* wrapped
output, not representing 360 degrees as an unsaturated video voltage.

`python tools/gen_adjacent50_pair.py` generates the BS source and the two
benchmark tables, checks all 32,768 Phase5 triplets and 65,536 raw pairs,
and runs 601 sequential pairs through the source-driven BS model.
Espressif's assembler accepts the generated BS program as three bundles
(one prime, two steady-state) and a 1024-word LUT16.

The standalone speed probe is built with ESP-IDF 6.0.2 after generating its
binary tables: `python tools/gen_adjacent50_pair.py`, then `idf.py build` in
`tools/cpu_phase_bench`. It targets ESP32-C5 at 240 MHz, uses 4096 distinct
raw pairs spread across the full 128 KiB table, and measures an unrolled
two-pair in-place loop as well as the scalar baseline. It writes its result
structure to offset `0x100000` after the internal-SRAM measurements so they
remain readable if a later cache test fails. This is a temporary test app;
back up and restore the receiver flash when testing on a shared board.

An initial C5 run measured roughly 8 cycles/raw byte for the simple scalar
phase loop. The pair-table result was not captured because the temporary app
stopped responding before it wrote its result. The optimized probe has built,
but its throughput is **not measured yet**.

## Live integration conditions

1. Measure the pair-LUT conversion on COM10 in flash and internal SRAM with
   `tools/cpu_phase_bench`; pass requires **strictly below 6 cycles/raw byte**
   with enough margin for ARC and DMA contention. The isolated benchmark is
   only a lower bound for the full receiver.
2. Reserve 128 KiB of internal SRAM or demonstrate flash-cache throughput
   under both strong and near-random weak IQ. The current menu raster is
   96 KiB and can share memory with the table because the menu stops flight,
   but that adds 32 KiB of static SRAM before other optimizations.
3. Transform completed RX descriptors before TX reads them. RX and TX are
   separated by half the 32 KiB ring at startup. Deadline checks must reject
   a mode switch before any unconverted raw byte reaches the phase backend.
4. ARC and diagnostics currently sample the raw ring. A flight-mode worker
   must hand them an unmodified raw snapshot before overwriting a descriptor;
   otherwise the gain loop reads Phase5 bytes as IQ.
5. Compare live sync, chroma, weak-signal noise, and fast gain recovery with
   unchanged Golden on the same VTX. A synthetic/math proof cannot guarantee
   a clean analog picture.
