# Live RF -> analog CVBS pipeline

The prepared producer ABI, bounded queue, conditioner, live PARLIO sink and
diagnostics are now specified in `research/live-stream-architecture.md`.

This document is the implementation boundary between the pieces C5VRX can
already build/test in software and the one undocumented silicon path that still
needs physical reverse engineering.

## Target end-to-end path

```text
5.8 GHz analog VTX
        |
        v
ESP32-C5 5 GHz receive front-end
        |
        v
continuous packed phase-bearing I/Q      <-- remaining silicon blocker
        |
        v
4:1 BitScrambler coarse FM discriminator
        |
        v
~20 MS/s biased phase-delta bytes
        |
        v
baseband level / polarity / filtering calibration
        |
        v
~20 MS/s CVBS samples
        |
        v
PARLIO + GDMA
        |
        v
6-bit passive DAC
        |
        v
75-ohm analog AV output
```

## What is implemented now

### Finite RF capture

The recovered vendor path can be triggered through:

```text
set_dump_mode(0)
adctrig(... sample_80m=1 ...)
0x40830000 / 64 KiB dump RAM
```

Firmware can:

- decode the packed signed 10-bit I/Q words;
- print finite captures;
- repeatedly re-trigger finite blocks;
- hash each block and measure I/Q jumps across block boundaries.

USB diagnostic:

```text
CHAIN 32 16384
```

This explicitly tests whether repeated finite dumps are useful as a temporary
chained source. It is **not** described as continuous capture.

### Hardware WBFM transform

`main/c5vrx_wbfm_4to1.bsasm` is a C5 BitScrambler program that intentionally
keeps one I/Q sample out of every four.

For each kept sample it performs a coarse I5/Q5 phase lookup and maintains the
previous kept phase in counter B. The low six counter bits produce:

```text
32 + phase[n] - phase[n-1]   modulo 64
```

where code 32 is the zero-frequency bias.

The LUT is 1024 x 16-bit = 2048 bytes and is loaded at initialization with
`bitscrambler_load_lut()` rather than being copied into assembly source.

Nominal rate relationship if the recovered RF mode really is 80 MS/s:

```text
80 MS/s packed I/Q -> 20 MS/s phase-delta bytes
```

The hardware implementation has three proof modes:

```text
WBFM HWTEST
```

Runs synthetic I/Q through the physical C5 BitScrambler and compares its bytes
against a CPU reference.

```text
WBFM CAPTURE 16384
```

Triggers a real finite vendor dump, copies it into DMA-capable SRAM and feeds
that actual RF block through the BitScrambler WBFM transform.

The firmware logs output code range, mean and mean absolute deviation from the
zero-frequency bias. This is the first direct on-device bridge from the
undocumented RF dump to hardware FM demodulation.

### Analog output

The output half is independent and already buildable:

```text
CVBS TEST
```

or the dedicated `sdkconfig.defaults.cvbs` image produces a PAL 625/50 test
raster through PARLIO/GDMA and the six-bit passive resistor DAC.

## What is deliberately not guessed

### 1. Continuous RF producer

The fixed `0x40830000` memory is a finite vendor debug dump. Static analysis has
not yet proven the hardware producer, descriptor/register sequence or a safe way
to turn it into a circular stream.

Do not create a fake software `while(1) adctrig()` path and call it continuous.
The `CHAIN` diagnostic exists to measure whether re-triggering is even useful.

### 2. Actual RF sample rate

The recovered ABI calls the mode `sample_80m`, but a physical signal must verify
exact complex sample rate and bandwidth. All 80 -> 20 MS/s language remains a
nominal architecture until that measurement exists.

### 3. CVBS scaling after WBFM

Real analog VTX deviation, discriminator polarity, DC offset and receive-filter
response must be measured from a real capture before freezing the transform from
phase-delta codes into the 0..63 CVBS DAC voltage codes.

That stage should remain a small streaming conditioner, not a PAL/NTSC pixel
decoder.

## Physical test sequence that unlocks the final software

The automated serial runner and exact pass/report format are documented in
`research/a4-bench-test.md`. Prefer that runner over copying terminal output by
hand.

Use A4 / 5805 MHz first so the RF center is a normal Wi-Fi center and arbitrary
retuning is removed from the experiment.

1. `WBFM HWTEST`
   - proves the BitScrambler program executes correctly on C5 silicon.
2. `CAPTURE 16384` with VTX off.
3. `CAPTURE 16384` with VTX on and a static image.
4. `WBFM CAPTURE 16384` with the same VTX.
5. Change image brightness and repeat.
6. `CHAIN 32 16384` with a stable source.
7. Measure actual capture period/rate against a controlled RF/FM source.
8. Inspect the vendor FE dump producer/register sequence using the resulting
   behavior plus static disassembly.

## Decision tree after CHAIN testing

### If chained dumps are nearly phase-contiguous

Implement a temporary ping-pong path:

```text
adctrig block A
 -> BitScrambler -> FM block A
adctrig block B
 -> BitScrambler -> FM block B
...
```

This may be sufficient to get the first recognizable live/near-live analog
image before the true producer is fully understood.

### If chained dumps have large gaps

Do not spend time hiding the gaps in video software. Reverse engineer the dump
producer itself and find the register/DMA mechanism feeding `0x40830000`.

### If the finite dump is not live 5 GHz RF

The direct C5-only receiver architecture fails its primary assumption. Fall back
to the documented project kill criterion: C5 synthesizer/LO plus a small
external mixer/IF detector, while keeping the same analog CVBS output half.

## Definition of "software complete enough for first live image"

Before a true live image, all of these can be completed without inventing
undocumented hardware behavior:

- finite packed-I/Q capture: **implemented**;
- chained finite-capture diagnostic: **implemented**;
- host exact WBFM reference: **implemented**;
- hardware-assisted 4:1 WBFM transform: **implemented/build-tested, physical HWTEST pending**;
- finite RF dump -> hardware WBFM bridge: **implemented, physical test pending**;
- PAL CVBS PARLIO output: **implemented/build-tested, physical monitor test pending**;
- passive CVBS DAC model/wiring: **implemented, scope test pending**.

The only component that cannot be completed honestly from static software work
alone is the undocumented **continuous live RF sample producer**. Once its
hardware interface is known, the rest of the pipeline has explicit software
entry/exit formats ready for integration.
