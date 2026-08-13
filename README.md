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

Digital display/USB preview work is explicitly secondary. If it is ever needed,
it should sit behind the proven analog receiver path or on a companion processor
rather than complicating the core VRX.

See [`research/analog-first-architecture.md`](research/analog-first-architecture.md).

---

## Output proof you can build before RF works

The output half has its own reproducible hardware test. The firmware can boot in
an **output-only PAL mode** that skips RF/Wi-Fi and continuously streams a
625/50 monochrome test raster through PARLIO/GDMA into a six-bit passive DAC.

```text
C5 RAM
  -> double-buffered PAL raster
  -> PARLIO/GDMA @ 20 MS/s
  -> GPIO0/1/6/8/9/10
  -> 6-bit source-matched resistor DAC
  -> 75-ohm CVBS input
```

Use [`sdkconfig.defaults.cvbs`](sdkconfig.defaults.cvbs) for the dedicated image
and follow [`research/devkit-cvbs-proof.md`](research/devkit-cvbs-proof.md) for
the exact resistor values, pin wiring and scope checklist.

The DevKitC CI artifact is named `c5vrx-pal-cvbs-proof-firmware`. It displays a
12-second C5VRX splash followed by a grayscale calibration screen. The separate
XIAO artifact is `c5vrx-xiao-pal-cvbs-proof-firmware`; use
[`research/xiao-c5-cvbs-proof.md`](research/xiao-c5-cvbs-proof.md), not the
DevKitC pin map. An optional 4.43361875 MHz swinging burst is included only as an
analog bandwidth/lock stress signal; it is not a claim of complete PAL color
encoding.

A host-side golden model in [`tools/pal_cvbs_reference.py`](tools/pal_cvbs_reference.py)
checks line/field timing, vertical sync, active-line count and DMA chunk wrap.
[`tools/minimal_cvbs_dac.py`](tools/minimal_cvbs_dac.py) separately validates the
ideal source-matched resistor network.

---

## Standalone receiver hardware target

The first custom receiver PCB should use **ESP32-C5-WROOM-1U-N4** rather than a
bare C5. That keeps the first board focused on proving the receiver instead of
simultaneously debugging a custom 5.8 GHz RF/crystal/flash implementation.

The recommended standalone architecture is:

```text
USB-C 5 V
   |
3.3 V LDO
   |
ESP32-C5-WROOM-1U-N4 <--- external 5.8 GHz antenna
   |
6-bit passive CVBS DAC
   |
AV视频 / RCA OUT
```

Complete BOM:

- [`hardware/analog-vrx-bom.md`](hardware/analog-vrx-bom.md) — design notes,
  wiring and functional-minimum versus standalone product BOM;
- [`hardware/analog-vrx-bom.csv`](hardware/analog-vrx-bom.csv) — machine-readable
  BOM for PCB/CAD work.

The functional minimum can be one active C5 module in the signal path plus the
video ladder and normal power/reset passives.

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

The decisive RF problem is whether the live 5 GHz receiver can feed it with
enough bandwidth and whether the producer can be converted from a finite debug
dump into a continuous/chained stream.

See [`research/adc-dump-format.md`](research/adc-dump-format.md),
[`research/reverse-engineering.md`](research/reverse-engineering.md) and
[`research/live-rx-pipeline.md`](research/live-rx-pipeline.md).

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

## Finite I/Q and live-pipeline diagnostics

### Test-readiness status

- **PROVEN IN SOFTWARE / BUILD TESTED:** modular RF block ABI, bounded queue,
  C5 BitScrambler WBFM, configurable sample conditioner, two-buffer PARLIO
  sink, branded PAL renderer, DevKitC and XIAO build profiles.
- **PHYSICAL TEST PENDING:** RF sample validity/rate/gaps, conditioner
  calibration, 75-ohm DAC levels and real monitor/VTX behavior.
- **CONTINUOUS RF PRODUCER UNKNOWN:** the C5 RF frontend producer feeding the
  prepared `c5vrx_rf_source_t` interface.

> **Can the ESP32-C5 provide a sufficiently continuous, phase-bearing RF
> stream for real-time WBFM demodulation?**

See [`research/live-stream-architecture.md`](research/live-stream-architecture.md).

Recommended first physical target:

```text
VTX:       A4 / 5805 MHz
C5 center: Wi-Fi ch161 / 5805 MHz
BW:        40 MHz
retune:    OFF
```

The USB protocol now exposes four useful proof commands:

```text
CAPTURE 16384
CHAIN 32 16384
WBFM HWTEST
WBFM CAPTURE 16384
NEARLIVE START
NEARLIVE STOP
PIPELINE STATS
```

`CAPTURE` gets a real finite packed-I/Q block. `CHAIN` repeatedly retriggers the
vendor dump and reports hashes plus boundary discontinuity, explicitly testing
whether finite captures are useful as a temporary near-live source.

`WBFM HWTEST` runs synthetic packed I/Q through the physical C5 BitScrambler and
compares its output with a CPU reference. `WBFM CAPTURE` bridges a real finite
vendor RF dump directly into that hardware WBFM transform.

`NEARLIVE START` exercises the complete bounded RF -> hardware WBFM ->
conditioner -> PARLIO route using repeated finite dumps. It is explicitly an
experimental finite/chained mode, not continuous capture.

For the first board test, use the guided runner instead of entering these by
hand. It captures the complete evidence in one JSON file:

```bash
python -m pip install pyserial
python tools/c5vrx_bench.py --port /dev/ttyACM0
```

See [`research/a4-bench-test.md`](research/a4-bench-test.md) for equipment,
safety prompts, automated gates and the separate PAL output test.

Decode serial captures on the host with:

```bash
python tools/decode_adc_dump.py capture.log --csv iq.csv --iq-bin iq.i16
python tools/wbfm_demod.py iq.i16 --dtype '<i2' --sample-rate 80000000 --output demod.f32
python tools/render_cvbs_lines.py iq.i16 --sample-rate 80000000 --output lines.pgm
```

The exact C5 capture sample rate still needs hardware verification.

---

## Analog CVBS output strategy

The mainline output target is:

```text
continuous phase-bearing RF samples
          ↓
4:1 hardware-assisted FM discriminator
          ↓
~20 MS/s biased phase-delta stream
          ↓
filter / polarity / level calibration
          ↓
~20 MS/s CVBS sample stream
          ↓
PARLIO + GDMA
          ↓
6 physical GPIO bits
          ↓
75-ohm weighted resistor DAC
          ↓
AV / goggles / DVR
```

PARLIO transports an 8-bit byte per sample while only six low data GPIOs are
physically connected. Six bits are the current reference compromise: useful
analog level resolution while still requiring only a handful of passives.

The existing `c5vrx_cvbs_out.c` experiment streams a full 625/50 interlaced
monochrome raster using two small DMA buffers rather than a complete framebuffer.

**Never connect raw 3.3 V GPIO outputs directly to a 75-ohm video input.**
Use the documented resistor network or another correctly scaled output stage.

---

## Hardware-assisted WBFM direction

A normal discriminator is:

```text
y[n] = angle(x[n] * conj(x[n-1]))
```

Doing that tens of millions of times per second on the CPU is unattractive.
C5VRX now has a C5 BitScrambler proof architecture instead.

```text
packed I10/Q10 words
       ↓
keep every fourth sample
       ↓
I5/Q5 -> 1024 x 16-bit phase LUT
       ↓
BitScrambler counter state
       ↓
32 + phase[n] - phase[n-1] modulo 64
       ↓
one output byte per four IQ words
```

If the recovered RF mode is physically confirmed as 80 MS/s, this architecture
maps naturally to the existing 20 MS/s CVBS path. The LUT occupies exactly the
C5's 2 KiB LUT RAM and is loaded once at initialization.

The assembly program is `main/c5vrx_wbfm_4to1.bsasm`; the C hardware bridge is
`main/c5vrx_wbfm_hw.c`; and `tools/validate_wbfm_bsasm.py` checks the numerical
architecture on the host.

This still does **not** claim continuous RF capture. The undocumented producer
feeding the finite vendor dump remains the final silicon-level blocker.

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

Experimental hardware paths remain off by default in the normal firmware.

---

## Repository layout

```text
.
├── hardware/
│   ├── analog-vrx-bom.md
│   └── analog-vrx-bom.csv
├── main/
│   ├── c5vrx_wifi5.c
│   ├── c5vrx_phy_hacks.c
│   ├── c5vrx_adc_dump.c
│   ├── c5vrx_wbfm_hw.c
│   ├── c5vrx_wbfm_4to1.bsasm
│   ├── c5vrx_cvbs_out.c
│   └── c5vrx_channels.c
├── research/
│   ├── analog-first-architecture.md
│   ├── live-rx-pipeline.md
│   ├── devkit-cvbs-proof.md
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
│   ├── validate_wbfm_bsasm.py
│   ├── pal_cvbs_reference.py
│   ├── minimal_cvbs_dac.py
│   ├── render_cvbs_lines.py
│   └── fpv_channel_report.py
├── sdkconfig.defaults.cvbs
└── README.md
```

---

## What still has to be proven

- Does the recovered dump capture the **live 5 GHz receive path** with an analog VTX?
- What is the actual effective sample rate and receive bandwidth?
- Does arbitrary frequency tuning move the real receiver center?
- Can the finite dump producer be tapped continuously or chained with tiny gaps?
- Does `WBFM HWTEST` give zero mismatches on actual C5 silicon and what sustained throughput is available?
- What polarity/gain/filtering maps real WBFM output to calibrated CVBS voltage codes?
- Does the streamed six-bit PARLIO resistor-DAC produce clean, correctly scaled PAL CVBS on physical hardware?
- Can the joined RF → WBFM → CVBS path keep latency low enough for FPV?

Until those are measured, C5VRX remains a research project rather than a
receiver you should fly with.

---

## Goal

> **One currently available ESP32-C5-class device doing the RF and WBFM work, then a tiny passive DAC producing normal analog CVBS.**

Prove each layer on hardware, keep the core architecture analog and streaming,
and add complexity only when a measurement proves it is necessary.
