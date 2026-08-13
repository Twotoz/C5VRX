<div align="center">
  <img src="assets/c5vrx-logo.jpg" alt="C5VRX logo" width="760" />

  <p><strong>ESP32-C5 analog 5.8 GHz FPV receiver research</strong></p>
  <p>An open-source attempt to turn commodity ESP32-C5 hardware into a tiny <strong>RX5808-class analog CVBS receiver</strong>.</p>

  <p>
    <img src="https://img.shields.io/badge/status-hardware%20validation%20next-blue" alt="Status" />
    <img src="https://img.shields.io/badge/chip-ESP32--C5-111111" alt="Chip" />
    <img src="https://img.shields.io/badge/RF-5.8%20GHz-6f42c1" alt="RF" />
    <img src="https://img.shields.io/badge/output-analog%20CVBS-orange" alt="Output" />
    <img src="https://img.shields.io/badge/goal-minimal%20hardware-00b894" alt="Goal" />
  </p>
</div>

---

## The decision: analog first, analog out

C5VRX is intentionally **not** trying to become a tiny software video player.
The primary product target is a classic low-latency analog receiver:

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
          ▼
 PARLIO + 6-bit resistor DAC
          │
          ▼
      75-ohm CVBS OUT
```

The important shortcut is that WBFM demodulation already recovers the composite
PAL/NTSC waveform. A normal analog monitor, DVR or goggles can decode that
waveform themselves, so C5VRX does **not** need a framebuffer, RGB conversion,
full PAL/NTSC pixel decoder or LCD controller in the main path.

Digital display/USB preview work is now explicitly secondary. If it is ever
needed, it should sit behind the proven analog receiver path or on a companion
processor rather than complicating the core VRX.

See [`research/analog-first-architecture.md`](research/analog-first-architecture.md).

---

## Why C5VRX?

Analog FPV still depends heavily on old 5.8 GHz receiver silicon such as
**RX5808 / RTC6715**. The ESP32-C5 is cheap, tiny and contains a real 2.4 +
5 GHz Wi-Fi RF chain.

C5VRX asks:

> **Can we bypass enough of the Wi-Fi PHY to use the C5 RF chain as a low-cost analog FPV receiver and reconstruct CVBS with almost no external active hardware?**

The long-term minimal hardware target is deliberately aggressive:

```text
ESP32-C5 / C5 module
power + decoupling
antenna / RF connection
6-bit weighted resistor DAC
AV connector
```

No RX5808. No external video decoder. No external video DAC. No AT7456E-class
OSD chip. No USB-UART bridge on a native-USB design.

---

## Current status

> **C5VRX is still experimental and is not yet a working analog FPV receiver.**

Static reverse engineering of Espressif's ESP32-C5 ESP-IDF v6.0.2 RF-test
binaries identifies a real finite complex-sample capture path:

```text
set_dump_mode()
      ↓
adctrig()
      ↓
0x40830000 dump RAM
      ↓
print_dump_data() / accumiq()
```

The vendor code decodes each 32-bit dump word as signed 10-bit I and Q samples:

```text
31                              20 19          10 9            0
┌────────────────────────────────┬──────────────┬──────────────┐
│      status / internal bits    │ I signed 10b │ Q signed 10b │
└────────────────────────────────┴──────────────┴──────────────┘
```

Recovered dump RAM:

```text
base: 0x40830000
size: 0x10000 bytes = 64 KiB
max:  16,384 complex samples
```

The decisive RF problem is no longer whether interesting low-level sample
machinery exists. It is whether the live 5 GHz receiver can feed it with enough
bandwidth and whether the producer can be converted from a finite debug dump
into a continuous/chained stream.

See [`research/adc-dump-format.md`](research/adc-dump-format.md) and
[`research/reverse-engineering.md`](research/reverse-engineering.md).

---

## FPV frequency coverage

The current direct C5 target window is **5645–5885 MHz**.

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

**Legend:** ✅ exact normal 5 GHz Wi-Fi center · ☑️ inside target range, needs
offset/arbitrary tuning · ❌ outside current target window.

Seven Band-A channels line up exactly with normal 5 GHz Wi-Fi centers, making
them useful bring-up targets before undocumented PLL tuning is required.

---

## RF bring-up and arbitrary tuning

The default firmware uses the normal ESP-IDF Wi-Fi driver for supported 5 GHz
bring-up. Promiscuous Wi-Fi receive is only a supported way to put the RF chain
on a real 5 GHz center; it does **not** expose analog FPV samples by itself.

The C5 PHY also contains an undocumented two-argument frequency path recovered
from disassembly:

```c
extern void phy_set_freq(uint16_t freq_mhz, int offset);
```

For example:

```c
phy_set_freq(5806, 0);
```

This remains opt-in until physical hardware proves that it moves the actual
receiver center.

See [`research/frequency-tuning.md`](research/frequency-tuning.md).

---

## Finite I/Q capture experiment

Recommended first physical test:

```text
VTX:       A4 / 5805 MHz
C5 center: Wi-Fi ch161 / 5805 MHz
BW:        40 MHz
retune:    OFF
ADC dump:  ON
raw print: ON
```

Capture at least:

```text
1. VTX off
2. VTX on, static image
3. VTX on, changing black/white image
```

Decode a serial dump on the host:

```bash
python tools/decode_adc_dump.py capture.log --csv iq.csv --iq-bin iq.i16
python tools/wbfm_demod.py iq.i16 --dtype '<i2' --sample-rate 80000000 --output demod.f32
python tools/render_cvbs_lines.py iq.i16 --sample-rate 80000000 --output lines.pgm
```

The exact C5 capture sample rate still needs hardware verification.

---

## Analog CVBS output strategy

The mainline output target is now:

```text
continuous phase-bearing RF samples
          ↓
quantized / hardware-assisted FM discriminator
          ↓
filter + decimate
          ↓
~20 MS/s unsigned CVBS sample stream
          ↓
PARLIO + GDMA
          ↓
6 physical GPIO bits
          ↓
75-ohm weighted resistor DAC
          ↓
AV output / goggles / DVR
```

PARLIO can transport an 8-bit byte per sample while only six low data GPIOs are
physically connected. Six bits are the current reference compromise: enough
voltage resolution for composite sync/blank/black/white separation while still
requiring only a handful of passives.

A nominal 3.3 V / 75-ohm direct-load reference network is documented in
[`research/analog-first-architecture.md`](research/analog-first-architecture.md).
It must be scope-validated on real hardware because GPIO output resistance,
logic-high droop, layout and the actual video load affect the final amplitude.

The existing `c5vrx_cvbs_out.c` experiment can independently generate a
20 MS/s PAL-line-like waveform before the RF half is working.

**Never connect raw 3.3 V GPIO outputs directly to a 75-ohm video input.**
Use the resistor network or another correctly scaled output stage.

---

## Real-time WBFM direction

A normal discriminator is:

```text
y[n] = angle(x[n] * conj(x[n-1]))
```

Doing that tens of millions of times per second on the CPU is unattractive.
C5VRX therefore explores the C5 BitScrambler as a DMA-side coarse phase
converter.

The realistic mainline experiment is one resident 2 KiB LUT:

```text
I10,Q10
   ↓ keep 5+5 MSBs
1024 x 16-bit LUT
   ↓
phase8 + (-phase8)
   ↓
state/counter subtraction
   ↓
delta phase
```

A second full 2 KiB phase-difference LUT cannot be resident at the same time on
a device with only one 2 KiB BitScrambler LUT, so the two-LUT fast model remains
a host-side research comparison rather than the preferred live architecture.

See [`research/dsp-pipeline.md`](research/dsp-pipeline.md) and
[`tools/bitlut_fm.py`](tools/bitlut_fm.py).

---

## What analog-first explicitly removes from the core design

The core receiver does **not** require these for its primary output target:

- a digital LCD or LCD controller;
- a full PAL/NTSC-to-RGB decoder;
- a full-frame video buffer;
- a UVC/high-speed USB video path;
- a separate video DAC IC;
- an AT7456E-style OSD chip.

Simple OSD remains possible later by modifying the recovered composite sample
stream after sync timing is known, without decoding the complete image to RGB.

---

## Build

Requires ESP-IDF with ESP32-C5 support. CI currently targets ESP-IDF v6.0.2.

```bash
idf.py set-target esp32c5
idf.py menuconfig
idf.py build
idf.py flash monitor
```

Experimental hardware paths remain off by default.

---

## Repository layout

```text
.
├── main/
│   ├── c5vrx_wifi5.c        # supported 5 GHz RF bootstrap
│   ├── c5vrx_phy_hacks.c    # opt-in undocumented tuning hooks
│   ├── c5vrx_adc_dump.c     # finite vendor I/Q capture experiment
│   ├── c5vrx_cvbs_out.c     # PARLIO composite resistor-DAC test
│   └── c5vrx_channels.c     # A/B/E/F/R frequency database
├── research/
│   ├── analog-first-architecture.md
│   ├── adc-dump-format.md
│   ├── dsp-pipeline.md
│   ├── video-output.md
│   ├── frequency-tuning.md
│   ├── reverse-engineering.md
│   └── roadmap.md
├── tools/
│   ├── analyze_phy.py
│   ├── decode_adc_dump.py
│   ├── wbfm_demod.py
│   ├── bitlut_fm.py
│   ├── render_cvbs_lines.py
│   └── fpv_channel_report.py
└── README.md
```

---

## What still has to be proven

- Does the recovered dump capture the **live 5 GHz receive path** with an analog VTX?
- What is the actual effective sample rate and receive bandwidth?
- Does arbitrary frequency tuning move the real receiver center?
- Can the finite dump producer be tapped continuously or chained with tiny gaps?
- Can one BitScrambler phase-LUT/state discriminator sustain the needed rate?
- Can the 6-bit PARLIO resistor-DAC produce clean, correctly scaled PAL/NTSC CVBS?
- Can the joined RF → WBFM → CVBS path keep latency low enough for FPV?

Until those are measured, C5VRX remains a research project rather than a
receiver you should fly with.

---

## Goal

> **One currently available ESP32-C5-class device doing the RF and WBFM work, then a tiny passive DAC producing normal analog CVBS.**

Prove each layer on hardware, keep the core architecture analog and streaming,
and add complexity only when a measurement proves it is necessary.
