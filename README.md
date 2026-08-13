<div align="center">
  <img src="assets/c5vrx-logo.jpg" alt="C5VRX logo" width="760" />

  <p><strong>ESP32-C5 analog 5.8 GHz FPV receiver research</strong></p>
  <p>An open-source attempt to turn commodity ESP32-C5 hardware into an <strong>RX5808-class analog FPV receiver</strong>.</p>

  <p>
    <img src="https://img.shields.io/badge/status-hardware%20validation%20next-blue" alt="Status" />
    <img src="https://img.shields.io/badge/chip-ESP32--C5-111111" alt="Chip" />
    <img src="https://img.shields.io/badge/RF-5.8%20GHz-6f42c1" alt="RF" />
    <img src="https://img.shields.io/badge/goal-RX5808%20alternative-00b894" alt="Goal" />
    <img src="https://img.shields.io/badge/video-analog%20WBFM-orange" alt="Video" />
  </p>
</div>

---

## Why C5VRX?

Analog FPV still depends heavily on old 5.8 GHz receiver silicon such as **RX5808 / RTC6715**. Those parts are increasingly awkward to source at sane prices.

The ESP32-C5 is cheap, tiny and contains a real **2.4 + 5 GHz Wi-Fi RF chain**. C5VRX asks a slightly unreasonable question:

> **Can we bypass enough of the Wi-Fi PHY to use that RF chain as a low-cost analog FPV receiver?**

The target architecture is now intentionally closer to a classic analog VRX than to a software video player:

```text
5.8 GHz analog FPV VTX
          │
          ▼
   ESP32-C5 RF front-end
          │
          ▼
      complex I/Q
          │
          ▼
 hardware-assisted WBFM
          │
          ▼
  sampled composite video
          │
     ┌────┴─────────────┐
     ▼                  ▼
PARLIO + DAC       PAL/NTSC decode
     │                  │
     ▼                  ▼
 analog CVBS       RGB / USB / LCD
```

The important shortcut is that **analog AV output does not require decoding PAL/NTSC into pixels**. WBFM demodulation already recovers the composite waveform, which can potentially be streamed straight to a DAC.

---

## Current status

> **C5VRX is still experimental and is not yet a working analog FPV receiver.**

But the project has moved past the original “maybe there is some hidden IQ path” stage.

### Static reverse-engineering breakthrough

Analysis of Espressif's **ESP32-C5 ESP-IDF v6.0.2 RF-test binaries** identifies a real finite complex-sample capture path:

```text
set_dump_mode()
      ↓
adctrig()
      ↓
0x40830000 dump RAM
      ↓
print_dump_data() / accumiq()
```

The vendor code decodes every 32-bit dump word as:

```text
31                              20 19          10 9            0
┌────────────────────────────────┬──────────────┬──────────────┐
│      status / internal bits    │ I signed 10b │ Q signed 10b │
└────────────────────────────────┴──────────────┴──────────────┘
```

So, statically, the C5 RF-test path contains **complex signed 10-bit I/Q samples**.

The recovered capture RAM is:

```text
base: 0x40830000
size: 0x10000 bytes = 64 KiB
max:  16,384 complex samples
```

The next decisive step is physical hardware validation: prove that this dump can capture a live **5.8 GHz analog FPV VTX** and that the samples retain enough bandwidth for WBFM video.

See [`research/adc-dump-format.md`](research/adc-dump-format.md).

---

## FPV frequency coverage

C5VRX is **not limited to Band A**. The current direct hardware target is **5645–5885 MHz**.

| Ch | A | B | E | F | R |
|---:|:---:|:---:|:---:|:---:|:---:|
| 1 | ✅ | ☑️ | ☑️ | ☑️ | ☑️ |
| 2 | ✅ | ☑️ | ☑️ | ☑️ | ☑️ |
| 3 | ✅ | ☑️ | ☑️ | ☑️ | ☑️ |
| 4 | ✅ | ☑️ | ☑️ | ☑️ | ☑️ |
| 5 | ✅ | ☑️ | ✅ | ☑️ | ☑️ |
| 6 | ✅ | ☑️ | ❌ | ☑️ | ☑️ |
| 7 | ✅ | ☑️ | ❌ | ☑️ | ☑️ |
| 8 | ☑️ | ☑️ | ❌ | ☑️ | ❌ |

**Legend:** ✅ exact normal 5 GHz Wi-Fi center · ☑️ inside target range, needs offset/arbitrary tuning · ❌ outside current C5 target window

Current out-of-range legacy channels are **E6 5905**, **E7 5925**, **E8 5945** and **R8 5917 MHz**.

### The fun coincidence

Seven Band-A channels line up exactly with normal 5 GHz Wi-Fi centers:

| FPV | MHz | Wi-Fi |
|:---:|---:|---:|
| A7 | 5745 | 149 |
| A6 | 5765 | 153 |
| A5 | 5785 | 157 |
| A4 | 5805 | 161 |
| A3 | 5825 | 165 |
| A2 | 5845 | 169 |
| A1 | 5865 | 173 |

That gives C5VRX clean bring-up frequencies before any undocumented PLL tricks are required.

---

## RF bring-up

The default firmware uses the **normal ESP-IDF Wi-Fi driver** to bring the C5 onto a supported 5 GHz Wi-Fi center:

```text
5 GHz-only mode
      ↓
20 / 40 MHz receive bandwidth
      ↓
standard Wi-Fi channel
      ↓
promiscuous receive
      ↓
read channel back
```

This is intentionally separate from the RF certification/test API. In ESP-IDF v6.0.2, the generic `esp_phy_wifi_rx()` header documents channel values `1–14`, so C5VRX does **not** pretend that passing `161` to that function is a supported 5 GHz tuner API.

Promiscuous Wi-Fi RX is only the **supported RF bootstrap**. It does not output analog FPV samples.

---

## Arbitrary frequency tuning

The C5 PHY contains a promising undocumented tuning path.

Actual ESP32-C5 v6.0.2 disassembly shows `phy_set_freq` consumes **two arguments** and reaches the real channel/RFPLL retune chain. C5VRX currently models the experimental ABI as:

```c
extern void phy_set_freq(uint16_t freq_mhz, int offset);
```

For an integer-MHz FPV channel such as RaceBand 5:

```c
phy_set_freq(5806, 0);
```

This is **disabled by default** until verified on real C5 hardware.

See [`research/frequency-tuning.md`](research/frequency-tuning.md).

---

## Finite I/Q capture experiment

C5VRX now includes an opt-in firmware experiment around the recovered vendor ADC dump path.

Enable in `menuconfig`:

```text
C5VRX research firmware
  → Run one finite vendor ADC/IQ dump after RF bring-up
```

Recommended first test:

```text
VTX:       A4 / 5805 MHz
C5 center: Wi-Fi ch161 / 5805 MHz
BW:        40 MHz
retune:    OFF
ADC dump:  ON
raw print: ON
```

Why A4 first? It removes arbitrary tuning from the equation. We only want to answer one question:

> **Does the C5 dump RAM contain live complex samples from an analog FPV transmission?**

Capture three logs:

```text
1. VTX off
2. VTX on, static image
3. VTX on, changing black/white image
```

The firmware can emit raw words as:

```text
C5VRX_IQ_BEGIN samples=8192 base=0x40830000
IQ:12345678
IQ:89abcdef
...
C5VRX_IQ_END
```

Decode them on a PC:

```bash
python tools/decode_adc_dump.py capture.log --csv iq.csv --iq-bin iq.i16
```

Then run the WBFM discriminator:

```bash
python tools/wbfm_demod.py iq.i16 --dtype '<i2' --sample-rate 80000000 --output demod.f32
```

Or immediately try to reconstruct the complete grayscale scanlines that fit inside the finite capture:

```bash
python tools/render_cvbs_lines.py iq.i16 --sample-rate 80000000 --output lines.pgm
```

The exact C5 sample rate still needs hardware verification; `80 MHz` is currently based on the recovered RF-test mode/ABI.

---

## Video output strategy

The first live-video target is **direct composite AV**, not a framebuffer.

```text
continuous I/Q
      ↓
quantized / hardware-assisted FM discriminator
      ↓
filter + decimate
      ↓
~20 MS/s 8-bit CVBS samples
      ↓
PARLIO + GDMA
      ↓
resistor DAC + video buffer
      ↓
75-ohm composite AV output
```

C5VRX now contains two experiments for this direction:

- `tools/bitlut_fm.py` models a **2 KiB BitScrambler LUT phase discriminator**, replacing per-sample trigonometry with a coarse hardware lookup.
- `c5vrx_cvbs_out.c` can independently loop a **20 MS/s PAL-line-like test waveform** through an 8-bit PARLIO bus, so the digital-to-CVBS hardware can be validated before continuous RF capture exists.

The PARLIO test is deliberately off by default and requires eight explicitly configured GPIOs plus a proper resistor DAC / output buffer. **Do not connect 3.3 V GPIOs directly to a 75-ohm video input.**

See [`research/video-output.md`](research/video-output.md) and [`research/dsp-pipeline.md`](research/dsp-pipeline.md).

For a digital LCD, C5VRX can continue past CVBS into sync detection, luma and later chroma decoding. The intended order is **analog CVBS first → grayscale digital video → color digital video**.

---

## Build

Requires ESP-IDF with ESP32-C5 support. CI currently targets **ESP-IDF v6.0.2**.

```bash
idf.py set-target esp32c5
idf.py menuconfig
idf.py build
idf.py flash monitor
```

Experimental paths are deliberately **off by default**.

---

## Repository layout

```text
.
├── main/
│   ├── c5vrx_wifi5.c        # supported 5 GHz RF bootstrap
│   ├── c5vrx_phy_hacks.c    # opt-in undocumented tuning hooks
│   ├── c5vrx_adc_dump.c     # finite vendor I/Q capture experiment
│   ├── c5vrx_cvbs_out.c     # 20 MS/s PARLIO composite-DAC test
│   └── c5vrx_channels.c     # A/B/E/F/R frequency database
├── research/
│   ├── adc-dump-format.md
│   ├── dsp-pipeline.md
│   ├── video-output.md
│   ├── frequency-tuning.md
│   ├── reverse-engineering.md
│   └── roadmap.md
├── tools/
│   ├── analyze_phy.py       # C5 libphy/librftest disassembly report
│   ├── decode_adc_dump.py   # raw dump → signed I/Q
│   ├── wbfm_demod.py        # exact offline FM discriminator
│   ├── bitlut_fm.py         # BitScrambler LUT discriminator model
│   ├── render_cvbs_lines.py # finite IQ → grayscale scanlines
│   └── fpv_channel_report.py
└── README.md
```

---

## What still has to be proven

The project now has a plausible path from C5 RF hardware to finite complex samples and a separate hardware path from digital samples to a composite-video DAC, but the important hardware questions remain:

- Does the recovered dump capture the **live 5 GHz receive path** when using an analog VTX?
- What is the actual effective sample rate / bandwidth of each RF-test capture mode?
- Does `phy_set_freq(5806, 0)` actually retune the physical receiver?
- Can finite dumps be chained, or can the producer feeding dump RAM be tapped continuously?
- Can the BitScrambler phase/discriminator idea sustain the required real sample rate?
- Can the PARLIO + resistor-DAC path produce clean, correctly scaled 75-ohm CVBS?
- Can all of this run with low enough latency for actual FPV?

Until those are measured, C5VRX remains a research project rather than a replacement module you should fly with.

---

## Contributing

RF reverse engineering, ESP32 PHY knowledge, RF-test tooling, DSP, PAL/NTSC decoding and real ESP32-C5 capture results are all very welcome.

When sharing a hardware capture, include the board, ESP-IDF version, VTX frequency, bandwidth, menuconfig options and raw serial log if possible.

---

## Goal

The end state is intentionally simple:

> **A tiny, inexpensive, open analog 5.8 GHz VRX built around a currently available ESP32-C5 instead of scarce RX5808-era silicon.**

No magic claims. Prove each layer, publish the ugly details, then make it fast.
