<div align="center">
  <img src="assets/c5vrx-logo.jpg" alt="C5VRX logo" width="760" />

  <p><strong>ESP32-C5 analog 5.8 GHz FPV receiver research</strong></p>
  <p>An experimental attempt to turn the ESP32-C5 into an open <strong>RX5808 alternative</strong> for analog FPV.</p>

  <p>
    <img src="https://img.shields.io/badge/status-research%20%2F%20proof--of--concept-blue" alt="Status" />
    <img src="https://img.shields.io/badge/chip-ESP32--C5-111111" alt="Chip" />
    <img src="https://img.shields.io/badge/band-5.8GHz-6f42c1" alt="Band" />
    <img src="https://img.shields.io/badge/goal-RX5808%20alternative-00b894" alt="Goal" />
    <img src="https://img.shields.io/badge/video-analog%20FPV-orange" alt="Video" />
  </p>
</div>

---

## Overview

**C5VRX** exists because analog FPV VRX parts such as the **RX5808 / RTC6715 family** are becoming harder to source, more expensive, or tied to increasingly awkward modules.

This project explores whether the **ESP32-C5** can be repurposed into a tiny, cheap, software-defined **5.8 GHz analog FPV receiver** by abusing its built-in **5 GHz Wi-Fi RF chain**.

The core idea is simple:

> If the ESP32-C5 can tune to FPV channel center frequencies, and if we can extract useful receive-domain data before normal Wi-Fi packet decoding, we may be able to demodulate analog FPV in software.

This would create a new path toward an **open, compact and affordable analog VRX platform** using commodity ESP32-C5 boards.

---

## Project status

> **Current status:** reverse-engineering / proof-of-concept.
>
> C5VRX is **not yet a working analog FPV receiver**.

What is already confirmed:

- The **ESP32-C5** has real **5 GHz receive hardware**.
- Several standard **FPV 5.8 GHz channels align directly with Wi-Fi 5 GHz channel centers**.
- Espressif's RF/PHY artifacts expose promising internal/debug-oriented functions related to:
  - ADC triggering
  - sample processing
  - IQ accumulation
  - RX buffer access
  - FE / dump / channel-dump style paths

What is not yet confirmed:

- Whether those internal paths provide **raw or near-raw receive-domain data** useful for analog FM video demodulation.
- Whether such data can be captured **reliably enough and fast enough** for real analog FPV.
- Whether the final path can reach **practical latency and image quality**.

---

## Why ESP32-C5?

The ESP32-C5 is interesting because it is:

- **Cheap**
- **Small**
- **Easy to buy**
- **5 GHz capable**
- **Open enough to experiment with** compared to fully closed consumer Wi-Fi modules

Even more importantly, several common **analog FPV frequencies line up exactly** with 5 GHz Wi-Fi channels.

### FPV ↔ Wi-Fi channel mapping

| FPV band/channel | Frequency (MHz) | Wi-Fi channel |
| --- | ---: | ---: |
| A7 | 5745 | 149 |
| A6 | 5765 | 153 |
| A5 | 5785 | 157 |
| A4 | 5805 | 161 |
| A3 | 5825 | 165 |
| A2 | 5845 | 169 |
| A1 | 5865 | 173 |

That means the **RF front-end does not need frequency translation tricks** just to land on those centers.

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

**Legend**
- ✅ = exact match with a normal 5 GHz Wi-Fi center frequency
- ☑️ = inside the C5VRX target range, but requires offset/arbitrary tuning
- ❌ = outside the current ESP32-C5 target range

### Current exceptions

- **RaceBand R8 — 5917 MHz** ❌
- **E6 — 5905 MHz** ❌
- **E7 — 5925 MHz** ❌
- **E8 — 5945 MHz** ❌

Everything else in the classic A/B/E/F/R table is inside the current C5VRX target window.

---

## Vision

If this works, C5VRX could become the basis for:

- a standalone **ESP32-C5 analog FPV receiver**,
- a tiny **OpenPocket/OpenVRX-style module**,
- a cheap research platform for **software-defined analog video reception**,
- or a future **community-built alternative** to scarce legacy VRX parts.

Long term, the dream is:

```text
Analog FPV VTX
      |
      v
5.8 GHz RF
      |
      v
ESP32-C5 RF front-end
      |
      v
raw / near-raw receive data
      |
      v
software WBFM demodulation
      |
      v
composite video reconstruction
      |
      v
display / DVR / OSD / downstream processing
```

---

## Current technical direction

The main attack path is focused on the **receive frontend dump / sample path**.

During analysis of ESP32-C5 RF/PHY artifacts, the following kinds of symbols stood out as especially interesting:

```text
adctrig
sampledeal
accumiq
get_rx_buffer
get_rx_data_addr
set_dump_mode
print_dump_data
loop_dump_test
fedump_rd_rxmem
phy_chan_dump_cfg_752
phy_adc_rate_set
phy_fe_adc_on
phy_iq_est_enable
phy_write_chan_freq
```

These names strongly suggest that Espressif's internal RF-test / PHY infrastructure includes functionality for:

- enabling receive-side analog or ADC paths,
- configuring frontend/channel dump behavior,
- accessing receive memory,
- estimating or accumulating IQ-related information,
- and interacting with the receive chain below ordinary Wi-Fi packet processing.

The **big question** is whether those pathways can expose a representation that still preserves enough information for **WBFM analog video demodulation**.

---

## Research strategy

C5VRX is intentionally split into clear stages.

### Phase 1 — Confirm RF-test receive baseline
- Build and flash a minimal ESP-IDF baseline.
- Start the official/vendor RF-test receive chain.
- Lock to a known analog FPV frequency, initially:
  - **5805 MHz**
  - **Wi-Fi channel 161**
  - **FPV Band A4**
- Verify stable 5 GHz receive operation.

### Phase 2 — Reverse engineer sample / dump paths
- Trace promising symbols in `librftest.a` and `libphy.a`.
- Recover call flow and candidate prototypes.
- Identify dump configuration paths.
- Determine what `fedump_rd_rxmem` and related functions actually return.

### Phase 3 — Controlled dump experiments
- Compare dump behavior with:
  - VTX off
  - VTX on
  - static test image
  - changing test imagery
  - weak vs strong signal
- Look for repeatable phase/frequency-bearing behavior.

### Phase 4 — Attempt analog demodulation
- If the dumped representation is usable, prototype:
  - FM discriminator / WBFM demodulation
  - sync extraction
  - PAL/NTSC line timing recovery
  - composite video reconstruction

### Phase 5 — Real receiver path
- Reduce overhead
- Improve stability
- Optimize latency
- Explore display / DVR / OSD output pipelines

---

## Repository structure

```text
.
├── main/        # ESP-IDF app entry point / baseline firmware
├── research/    # notes, reverse-engineering docs, generated findings
├── tools/       # scripts for symbol discovery and binary analysis
└── README.md
```

### Important files

- `main/` — baseline firmware used to enter RF-test RX mode on the ESP32-C5.
- `tools/analyze_phy.py` — helper script to locate and inspect relevant PHY/RF blobs.
- `tools/check_symbols.sh` — quick symbol scan for promising functions.
- `research/reverse-engineering.md` — active reverse-engineering notes / attack plan.
- `research/roadmap.md` — milestone overview.

---

## Getting started

### Requirements

- An **ESP32-C5** development board
- A recent **ESP-IDF** with ESP32-C5 support
- An analog FPV VTX for testing
- Preferably a low-power close-range test setup

### Build

```bash
idf.py set-target esp32c5
idf.py build
```

### Flash + monitor

```bash
idf.py flash monitor
```

The baseline firmware starts Espressif's RF certification/test receive path on:

- **Channel 161**
- **5805 MHz**
- **HT40** as an initial wide-band experiment

> Note: the baseline does **not** receive analog video yet.
> It only proves that the C5 can be driven into the relevant 5 GHz RX path while we continue reverse-engineering the internal dump/sample flow.

---

## Binary analysis helpers

### Run focused RF/PHY analysis

```bash
python tools/analyze_phy.py
```

This script looks for the ESP32-C5 PHY/RF libraries in your ESP-IDF installation, runs the appropriate RISC-V analysis tools, and writes results into `research/generated/`.

### Quick symbol scan

```bash
./tools/check_symbols.sh
```

This is useful for quickly checking whether the expected C5 RF-test / PHY symbols are present in the installed toolchain.

---

## First hardware experiment

A simple first experiment looks like this:

1. Set an analog FPV VTX to **5805 MHz / Band A4**.
2. Keep TX power low and distance short.
3. Flash the baseline C5VRX firmware.
4. Confirm the ESP32-C5 enters the expected 5 GHz test RX path without crashing.
5. Analyze the vendor blobs and identify the dump function path.
6. Add a minimal experiment around the dump mechanism.
7. Compare captured data with VTX off vs on.
8. Then vary the transmitted image and see whether the dump output changes in a structured way.

---

## Design goals

- **Create an RX5808 alternative** using widely available hardware
- Stay focused on **analog FPV**, not generic Wi-Fi sniffing
- Prefer **small, cheap dev boards** for proof-of-concept
- Keep the end goal **low latency**
- Document findings properly instead of relying on magic undocumented code
- Separate **verified behavior** from **hypotheses**
- Build something the FPV/open-source community can actually use later

---

## Non-goals (for now)

At this stage, C5VRX is **not** trying to be:

- a polished consumer VRX,
- a finished DVR platform,
- a long-range digital video system,
- or a perfect software-defined radio framework.

The immediate goal is much narrower:

> **Can the ESP32-C5 be pushed far enough to become a practical analog 5.8 GHz FPV receiver core?**

---

## Risks / unknowns

This project is exciting, but there are real unknowns:

- The internal receive dump path may not expose sufficiently raw data.
- The sample format may be heavily processed or lossy.
- Timing / bandwidth may be insufficient for usable video reconstruction.
- Some needed functions may rely on hidden side effects or undocumented register state.
- The analog path may prove possible but too fragile for a clean end product.

That is why C5VRX is treated as **research first, product second**.

---

## Contributing

Contributions are welcome, especially around:

- RF / PHY reverse engineering
- ESP32-C5 undocumented behavior
- analog FM / WBFM demodulation
- composite video decoding
- test capture analysis
- toolchain automation

If you experiment with C5VRX, please try to share:

- board used
- ESP-IDF version
- exact VTX frequency
- code changes
- logs / captures
- what changed between off/on-signal tests

That will help separate signal from noise much faster.

---

## Related context

C5VRX is closely related in spirit to projects like **OpenPocket** and other open FPV tooling efforts: finding clever, practical ways to keep analog FPV hardware accessible, hackable and fun.

---

## Disclaimer

C5VRX is experimental research software.

Use it responsibly, follow local RF rules, and be careful when testing around active transmitters. Nothing here guarantees regulatory compliance, video quality, or safe operation for mission-critical use.

---

## License

**TBD** before first public release.

For now, treat the repository as an active research project.
