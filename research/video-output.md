# Video output architecture

The easiest C5VRX video path is **not** to decode PAL/NTSC into pixels.

For an analog FPV receiver, WBFM demodulation already gives us the original
**composite video baseband (CVBS)**. If the goal is an analog AV output, we can
keep the signal as a sampled waveform and send it straight to a small DAC.
That avoids frame buffers, color decoding and most per-pixel work.

## Preferred low-latency path

```text
5.8 GHz analog FPV RF
        |
        v
ESP32-C5 RF / complex I,Q
        |
        v
hardware-assisted FM discriminator
        |
        v
8-bit composite samples
        |
        v
PARLIO + GDMA @ ~20 MS/s
        |
        v
6/8-bit resistor DAC + video buffer
        |
        v
1 Vpp / 75 ohm CVBS
        |
        v
analog LCD / AV input / DVR
```

The key point is that **PAL/NTSC decoding is skipped entirely** on this path.
Horizontal sync, vertical sync, luma and chroma remain inside the reconstructed
composite waveform exactly as they would at the output pin of a conventional
analog VRX.

## Why PARLIO is unusually useful here

ESP32-C5 has a dedicated Parallel IO peripheral connected to GDMA. In
half-duplex mode it supports an **8-bit parallel bus** and the C5 datasheet
specifies a clock of **up to 40 MHz**.

That makes an 8-bit video DAC practical without bit-banging GPIOs:

```text
RAM byte 0  -> GPIO DAC value 0
RAM byte 1  -> GPIO DAC value 1
RAM byte 2  -> GPIO DAC value 2
...
             GDMA
              |
              v
         PARLIO 8-bit
              |
              v
        resistor DAC
```

A 20 MHz byte stream is 160 Mbit/s on the parallel data bus but requires no
160 Mbit/s serial interface: eight GPIOs change together once every 50 ns.
PARLIO/GDMA performs the actual output timing while the CPU prepares or swaps
buffers.

A final board would need proper output scaling/buffering for a 75-ohm video
load. **Do not connect eight 3.3 V GPIOs directly to an RCA input.** A resistor
DAC plus a suitable buffer/attenuator is the intended hardware experiment.

## The real compute problem is FM demodulation

The expensive part is earlier in the chain.

A normal complex FM discriminator is:

```text
y[n] = angle(x[n] * conj(x[n-1]))
```

At tens of millions of I/Q samples per second, doing complex multiplies and an
`atan2`-style operation on the 240 MHz RISC-V CPU is a poor architecture. Even
if it can be heavily optimized, it burns most of the CPU budget before video
filtering or output starts.

So C5VRX should try to turn the discriminator into a **DMA-side lookup problem**.

## BitScrambler FM experiment

ESP32-C5 includes a BitScrambler on the DMA path. The hardware can process up
to 32 bits per DMA clock period, has two small counters, an output-history
source and **2048 bytes of LUT RAM**. It is intended for format transforms but
is programmable enough to investigate a coarse phase discriminator.

### Pass 1: I/Q -> phase

The recovered RF-test sample format contains signed 10-bit I and Q. Keep only
the five most-significant bits of each component:

```text
I10 -> I5 --+
            +--> 10-bit LUT address --> phase8
Q10 -> Q5 --+                         --> -phase8
```

There are exactly `32 x 32 = 1024` coarse I/Q cells.

A `1024 x 16-bit` table occupies exactly **2048 bytes**, so every LUT entry can
contain:

```text
low byte:   phase8
high byte: -phase8 modulo 256
```

No multiply and no trigonometry are required at runtime.

### Pass 2A: quality path

Use the BitScrambler counters to perform modular phase subtraction:

```text
phase8[n] + (-phase8[n-1]) -> delta phase8
```

`tools/bitlut_fm.py` models this path. On the synthetic video-like WBFM test it
currently gives roughly:

```text
phase-error RMSE: ~0.033 rad/sample
correlation:      ~0.988
```

This is a numerical approximation test only. It is **not** a measured C5
throughput result.

### Pass 2B: throughput path

If the counter version needs too many BitScrambler cycles, a second 2 KiB LUT
can trade precision for throughput:

```text
current phase6  --+
                   +--> 11-bit address --> signed delta8
previous phase5 --+
```

The 11-bit address gives 2048 combinations and therefore exactly fills an
8-bit-wide 2 KiB LUT. The current host simulation is visibly noisier but still
tracks the synthetic FM waveform. This is the fallback when DMA throughput is
more important than phase precision.

Generate/test the tables with:

```bash
python tools/bitlut_fm.py --self-test
python tools/bitlut_fm.py --emit-dir generated-luts
```

## Target sample-rate experiment

The existing RF-test dump code currently asks `adctrig()` for its 80 MHz sample
mode. Historical Espressif tooling suggests the `sample_80m` argument selects
between capture-rate modes, but C5VRX must verify the actual C5 rate on hardware
before treating a lower mode as 40 MS/s.

If a verified ~40 MS/s continuous complex path exists, an attractive pipeline
would be:

```text
~40 MS/s packed I/Q
        |
        v
BitScrambler phase/discriminator
        |
        v
decimate / filter
        |
        v
~20 MS/s unsigned CVBS samples
        |
        v
PARLIO 8-bit @ ~20 MHz
```

Twenty megasamples per second is comfortably below PARLIO's documented 40 MHz
clock ceiling and is enough to preserve the roughly 4.43 MHz PAL chroma region
while leaving transition/filtering room.

The **unproven blocker remains continuous RF sample production**, not PARLIO.
The current recovered dump RAM only gives a finite 64 KiB capture.

## Digital output options

### Direct LCD from the C5

For a digital LCD we cannot simply send composite samples. We need at least:

```text
CVBS samples
  -> horizontal/vertical sync detection
  -> active-line extraction
  -> luma reconstruction
  -> optional PAL/NTSC chroma decode
  -> RGB/YUV pixels
  -> LCD interface
```

This costs more compute than analog CVBS output. A good milestone is **grayscale
first**: line sync + luma only, one or two line buffers, no full-frame buffer.
Color can be added later.

### USB-C preview

ESP32-C5's built-in USB Serial/JTAG controller is USB 2.0 **full speed**, up to
12 Mbit/s. It is excellent for flashing, control and finite captures, but it is
far too slow for raw wideband I/Q and does not provide a native high-speed UVC
video device path.

So C5VRX Control should eventually receive one of these instead:

- low-resolution luma frames,
- line-decoded grayscale data,
- heavily decimated diagnostic video,
- or short IQ/CVBS captures.

Raw 40 MS/s I/Q over the existing USB-C port is not a realistic target.

### High-speed companion output

The C5 also has an SDIO slave peripheral with 4-bit mode, DMA and a documented
clock range up to 50 MHz. That creates a much better future route to an
ESP32-P4/FPGA/other host **after FM demodulation has reduced the stream to an
8-bit composite representation**.

Conceptually:

```text
C5 RF + WBFM
     |
     +--> PARLIO DAC --> analog CVBS       (cheapest / lowest latency)
     |
     +--> SDIO --------> companion MCU     (digital display / USB HS / DVR)
     |
     +--> USB CDC -----> low-res preview   (development/debug)
```

## Recommended development order

1. Prove a real 5.8 GHz finite I/Q capture.
2. Verify the actual `adctrig()` sample rate(s).
3. Feed real captures through `bitlut_fm.py` and compare against the exact host discriminator.
4. Reverse-engineer the FE dump producer until captures can be chained/ring-buffered.
5. Implement the BitScrambler phase LUT on real C5 hardware and benchmark throughput.
6. Independently prove PARLIO -> resistor DAC -> valid composite waveform.
7. Join both halves into the first live analog C5VRX AV output.
8. Only then spend compute on digital PAL/NTSC decoding and USB/LCD preview.

## Why this is preferable to decoding everything first

A conventional analog VRX does not understand pixels. It demodulates FM and
outputs composite video. C5VRX should copy that architecture as closely as the
C5 hardware allows.

If the BitScrambler experiment works, the end product can potentially look much
more like a **streaming hardware receiver** than an MCU repeatedly running a
large software video decoder.

## Primary hardware references

- ESP32-C5 Series Datasheet, v1.3: CPU up to 240 MHz, PARLIO up to 40 MHz, GDMA, BitScrambler and SDIO slave.
- ESP-IDF ESP32-C5 Parallel IO TX documentation: GDMA-backed parallel transmission and BitScrambler decoration support.
- ESP-IDF ESP32-C5 BitScrambler documentation: 2 KiB LUT, input/output routing, counters and memory-to-memory mode.
- ESP32-C5 USB Serial/JTAG documentation: USB 2.0 full-speed / 12 Mbit/s.
