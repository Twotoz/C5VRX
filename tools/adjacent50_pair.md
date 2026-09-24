# Adjacent50 pair-LUT candidate

This is an experimental live mode for the XIAO ESP32-C5 and the existing
resistor DAC. The first hardware run produced video with heavy static and later
crashed. The cause is not yet proven. Golden remains the default. The next
build bounds the descriptor worker, records its worst processing time and
backlog, and reboots into Golden if it misses the ring deadline. The first
attempt to restart PARLIO TX in place hung in `parlio_tx_do_transaction()` and
triggered the task watchdog; a full restart avoids that recovery deadlock.
The first periodic live counters showed roughly 21,000 transformed descriptors
per second, zero detected TX overlaps, a maximum backlog of three descriptors,
and only occasional single-block budget misses. That weakens the original
CPU-deadline explanation for the static. The next instrumented build measures
actual transformed bytes per second, RX descriptor count and span, and rejects
backwards descriptor-pointer observations that could cause a second transform
of the same raw IQ block.

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
two-pair in-place loop. It prints its result over USB Serial/JTAG. This is a temporary test app;
back up and restore the receiver flash when testing on a shared board.

On COM10 at 240 MHz, the isolated SRAM pair-table probe measured 4.555 to
4.557 cycles/raw byte for the out-of-place loop, 4.128 for the in-place loop,
and **3.754 for the unrolled in-place loop**, all with zero wrong 16-bit words
on the 4096-pair spread input. This leaves about 2.246 CPU cycles/raw byte
for DMA contention and all other work. The previous simple scalar phase loop
measured about 8 cycles/raw byte. The earlier probe called
`esp_partition_erase_range` on its own running factory partition and rebooted
after printing the valid pair measurements; the receiver flash was restored
and verified afterward. The probe no longer attempts that flash write.

## Live integration conditions

1. Measure the in-place loop while RX/TX DMA and ARC are active. The isolated
   3.754 cycles/raw byte is only a lower bound for the full receiver. The first
   live test showed heavy static and a later crash, so this condition remains
   open until the ring telemetry identifies the cause.
2. Reserve 128 KiB of internal SRAM or demonstrate flash-cache throughput
   under both strong and near-random weak IQ. The current menu raster is
   98,304 bytes and can share memory with the table because the menu stops
   flight. The remaining 32,768 bytes can hold the menu's 25,600 bytes of
   GDMA descriptors too, leaving one 128 KiB overlay and no menu descriptor
   heap allocation. The extra 32,768 static bytes over main still need a
   boot/runtime heap check; the pair table must be copied from flash after
   every menu exit before enabling the pair demodulator again.
3. Transform completed RX descriptors before TX reads them. RX and TX are
   separated by half the 32 KiB ring at startup. Deadline checks must reject
   a mode switch before any unconverted raw byte reaches the phase backend.
4. ARC and diagnostics currently sample the raw ring. A flight-mode worker
   must hand them an unmodified raw snapshot before overwriting a descriptor;
   otherwise the gain loop reads Phase5 bytes as IQ.
5. Compare live sync, chroma, weak-signal noise, and fast gain recovery with
   unchanged Golden on the same VTX. A synthetic/math proof cannot guarantee
   a clean analog picture.
