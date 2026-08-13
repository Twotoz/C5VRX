# Analog FPV DSP pipeline

Once C5VRX obtains phase-bearing receive samples, the most important design choice is **where to stop decoding**.

For a classic analog AV output we do **not** need to decode PAL/NTSC into pixels. WBFM demodulation already recovers the original composite-video waveform, so the lowest-latency path is to stream that waveform straight into a DAC.

## Two output paths

```text
5.8 GHz analog FPV RF
        |
        v
C5 RF frontend / ADC
        |
        v
complex / phase-bearing I,Q
        |
        v
FM discriminator
        |
        v
composite video samples (CVBS)
        |
        +------------------------------+
        |                              |
        v                              v
  ANALOG FAST PATH                DIGITAL PATH
 PARLIO + GDMA DAC             sync / luma / chroma
        |                              |
        v                              v
 1 Vpp / 75 ohm CVBS              RGB / YUV pixels
        |                              |
        v                              v
 AV LCD / DVR / goggles           LCD / USB / DVR
```

The analog path behaves much more like an RX5808: demodulate RF and reconstruct the composite voltage. Horizontal sync, vertical sync, luma and color stay encoded in CVBS and therefore do not consume CPU as a video-decoder workload.

See [`video-output.md`](video-output.md) for the proposed hardware architecture.

## FM discriminator

For complex samples `x[n] = I[n] + jQ[n]`, the reference discriminator is:

```text
y[n] = angle(x[n] * conj(x[n-1]))
```

`y[n]` is the phase increment in radians/sample. Converting it to instantaneous frequency is:

```text
f[n] = y[n] * sample_rate / (2*pi)
```

This is mathematically simple, but doing it tens of millions of times per second on the 240 MHz application CPU is still expensive. The CPU implementation therefore remains the **reference**, not the preferred final architecture.

## Hardware-assisted discriminator experiment

C5VRX now explores using the ESP32-C5 **BitScrambler** in the DMA path as a quantized phase discriminator.

The recovered RF-test format provides signed 10-bit I and Q. The first experiment reduces each to the five most-significant bits:

```text
I10 -> I5 --+
            +--> 1024-entry LUT --> phase8 + (-phase8)
Q10 -> Q5 --+
```

A 1024-entry table with a 16-bit result occupies exactly 2048 bytes. Every result can therefore contain an 8-bit quantized phase plus its modular negative.

A second stage then approximates:

```text
phase[n] - phase[n-1]
```

without complex multiplication or trigonometry. `tools/bitlut_fm.py` models two variants:

- a higher-quality phase8/counter-subtraction path;
- a lower-precision 2048-entry phase-difference LUT for a possible higher-throughput implementation.

Run:

```bash
python tools/bitlut_fm.py --self-test
```

This test proves only the **numerical approximation**, not real BitScrambler throughput or a continuous C5 receive path.

## Analog CVBS output target

If continuous RF samples become available, the first live-video target is:

```text
wideband I/Q
    |
    v
hardware-assisted WBFM
    |
    v
filter / decimate
    |
    v
~20 MS/s unsigned composite samples
    |
    v
PARLIO + GDMA, 8-bit
    |
    v
resistor DAC + 75-ohm video buffer
    |
    v
CVBS OUT
```

This deliberately avoids a framebuffer. Ideally C5VRX uses ping-pong line/stream buffers so latency is dominated by RF/DSP filtering rather than a complete video frame.

## Digital path

Direct digital video requires extra work because a display wants pixels rather than a composite voltage:

```text
CVBS
  -> horizontal/vertical sync detector
  -> active-line window
  -> luma
  -> optional chroma demodulation
  -> YUV/RGB
  -> LCD / host
```

The first digital milestone should therefore be **grayscale**. Luma plus sync is enough to prove actual images with a tiny line buffer. PAL/NTSC color decoding can follow later.

For USB development, short IQ/CVBS captures and low-resolution decoded previews are much more realistic than raw wideband I/Q streaming.

## Host-side validation

`tools/wbfm_demod.py` implements the exact reference discriminator for interleaved IQ captures. It deliberately runs on a PC first so we can answer the most important question quickly: **does a C5 dump contain real analog-FPV phase information?**

Run the synthetic test:

```bash
python tools/wbfm_demod.py --self-test
```

For a future little-endian int16 IQ capture:

```bash
python tools/wbfm_demod.py capture.iq \
  --dtype '<i2' \
  --sample-rate 20000000 \
  --output demod.f32
```

The output contains float32 instantaneous-frequency samples in Hz.

## What to look for in the first real capture

Before trying to render an image, validate progressively:

1. A known CW signal should produce a nearly constant discriminator output.
2. A frequency-modulated test source should reproduce its modulation waveform.
3. An analog FPV VTX with a static image should show repeatable horizontal-sync structure.
4. Changing image brightness should measurably change the recovered composite waveform.
5. Reconstruct a few grayscale scanlines from a finite capture.
6. Only after continuous capture exists, connect the streaming discriminator to the CVBS DAC path.
7. Decode PAL/NTSC to digital pixels only when a digital output actually needs it.

This keeps RF reverse engineering, FM demodulation, analog output and full video decoding as separate problems.