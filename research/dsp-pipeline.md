# Analog FPV DSP pipeline

Once C5VRX obtains phase-bearing receive samples, analog FPV demodulation itself is straightforward compared with the RF-access problem.

## Expected receive chain

```text
5.8 GHz analog FPV RF
        |
        v
C5 RF frontend / ADC
        |
        v
complex or phase-bearing samples
        |
        v
FM discriminator
        |
        v
composite baseband
        |
        +--> sync detection
        +--> luma
        +--> chroma / color subcarrier
        |
        v
PAL / NTSC image
```

## FM discriminator

For complex samples `x[n] = I[n] + jQ[n]`, a low-cost discriminator is:

```text
y[n] = angle(x[n] * conj(x[n-1]))
```

`y[n]` is the phase increment in radians/sample. Converting it to instantaneous frequency is:

```text
f[n] = y[n] * sample_rate / (2*pi)
```

This avoids an explicit `atan2(Q, I)` phase unwrap over the whole stream and maps well to a later fixed-point implementation.

## Host-side validation

`tools/wbfm_demod.py` implements this discriminator for interleaved IQ captures. It deliberately runs on a PC first so we can answer the most important question quickly: **does a C5 dump contain real analog-FPV phase information?**

Run the synthetic test:

```bash
python tools/wbfm_demod.py --self-test
```

The test generates a complex FM waveform, demodulates it and checks reconstruction error. It does not prove C5 reception; it proves the downstream discriminator is ready when captures arrive.

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
5. Only then attempt PAL/NTSC line reconstruction.

This keeps RF reverse engineering and video decoding as separate problems.
