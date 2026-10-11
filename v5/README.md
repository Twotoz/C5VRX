# C5VRX-5

Analog 5.8 GHz FPV receiver on one Seeed Studio XIAO ESP32-C5 with a passive
6-bit resistor DAC: live FM video in, composite PAL/NTSC out for goggles.

C5VRX-5 is the simplest and best C5VRX so far: one demodulator, no
experimental options. Everything that is in it earned its place on the
board or in held-out tests (see [docs/FINDINGS.md](docs/FINDINGS.md)).

## What it does

- **U85Q demodulator** in the TX BitScrambler: an untrained 32-phase tracker
  that keeps the picture locked across VTX deviations, off-centre carriers
  and multipath instead of dropping out. Board-validated on real video.
- **Fixed ultrafine IQ** (ADC bits 9,6,5,4) at 40 MS/s, no CPU in the sample
  path.
- **Direct Gain V5**: firmware gain with a zero-write healthy band, fast
  overload handling and a learned, persisted gain map.
- **Narrow analog filter**, measured once per board (VTX off): the lowest
  noise bandwidth that still passes 14 MHz of video.
- **Automatic receive-chain care**: per-gain DC correction, DC drift
  tracking, sampling-phase check, AFC that centres the VTX.
- **Simple menu**: band, channel, scan, video standard, exit.

## Hardware

XIAO ESP32-C5. DAC on D4..D9 (GPIO 23, 24, 11, 12, 8, 9; LSB first) with
8.2k / 3.9k / 2k / 1k / 470R / 240R and a 200R load, as in C5VRX-3/4. The
receiver uses the ESP32-C5's own Wi-Fi radio; no extra RF parts.

## First boot

1. Flash (below) and power the receiver with the **VTX off** for about a
   minute: it measures its narrow filter and the DC correction while there
   is no signal. Both are stored.
2. Switch the VTX on. Without a stored channel the receiver starts on
   A1 (5865 MHz); use the menu or scan to find yours.

## Controls (BOOT button)

| live video | in the menu |
| --- | --- |
| short press: next channel in the band | short press: next item |
| hold 0.6 s: open the menu | hold 0.6 s: do the item |

Menu items: **BAND** (next band), **CHANNEL** (next channel), **SCAN**
(all 48 channels, picks the strongest centred analog video), **VIDEO**
(AUTO / NTSC / PAL for the menu picture; AUTO follows the camera), **EXIT**.
The menu closes by itself after 30 s without a press.

## Flashing

- **Web flasher**: [twotoz.github.io/C5VRX](https://twotoz.github.io/C5VRX/),
  tab **C5VRX-5**, *Full firmware* when coming from another generation.
- **ESP-IDF 6.0.2**: `cd v5 && idf.py build flash`. The filter and DC
  correction need the exact libphy of IDF 6.0.2 (checked at build time);
  with another PHY they are disabled and the build warns.
- A running receiver enters USB download mode from the console key `` ` ``
  (`tools/enter_download.py`), so no BOOT button is needed for flashing.

## Serial console (USB, 115200)

`T` status, `I` IQ snapshot (`tools/capture_iq_snapshot.py`), `o` menu,
`c` next channel, `b` next band, `s` scan, `F` re-measure the filter (VTX
off), `` ` `` download mode, `h` help.

## Credits and license

C5VRX-5 is part of [C5VRX](https://github.com/Twotoz/C5VRX) by Twotoz and
the C5VRX contributors; the official website
([twotoz.github.io/C5VRX](https://twotoz.github.io/C5VRX/)) has the project
and its Discord invite. It builds on C5VRX-2..4 (the continuous MODEM_DIAG
to BitScrambler video path, Direct Gain V3-V5, the PAIR schedule and the
U85Q study of PR #190), on zerowidth/C5VRX PR #3 (interference-burst gate,
sampling-phase probe, off-grid channel tuning), on Louis Hitchcock's staged
overload recovery (C5VRX #182/#184), and on ESPARGOS esp-sdr (GPL-3.0) for
the C5 RX filter control. Espressif supplied the GDMA EOF fix
(espressif/esp-idf#19091).

Like the rest of this repository, C5VRX-5 is licensed under
**GPL-3.0-only** (see `LICENSE` at the repository root). The GPL allows
commercial use, including selling receivers with this firmware, provided
the source (with your changes) is passed on under the same license.
