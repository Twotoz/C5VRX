# C5VRX repository grounding

`Twotoz/C5VRX` is the canonical project repository.

- Current implementation: `/main`
- Current hardware-proven findings: `/docs`
- Historical experiments: `/legacy/c5vrx1`
- Preserved archive discussions: `/docs/legacy-issues`

`legacy/c5vrx1` is reference material, not current production code. Always
search it when investigating ESP32-C5 RF/PHY, MODEM_DIAG, IQ capture, analog
video, WBFM, PARLIO, DAC hardware, receiver-console behavior, or an architecture
that may already have been attempted.

When historical assumptions conflict with newer physical C5VRX evidence,
current hardware findings in `/docs` take precedence. Do not reintroduce a
rejected architecture before reading the corresponding current and legacy
findings that explain its failure.

## Current realtime invariants

- VTX presence and USB must never gate or pace IQ production.
- The normal live source is MODEM_DIAG Q4/I4 captured by PARLIO RX; active
  MAC-owned dump SRAM is a diagnostic writer, not a readable live source.
- Do not turn a physical SRAM or DMA block boundary into a DSP reset.
- Do not claim sample-gapless RF or AV transport without its physical proof.
- The normal live path recovers the transmitted composite waveform; it does not
  decode pixels or regenerate PAL/NTSC.
- Keep USB/debug outside realtime pacing.
- Do not silently change the tested XIAO D4..D9 DAC pin order or the physical
  8.2k/3.9k/2k/1k/470R/240R plus 200R network.

## Desktop build and flash workflow

- `tools/c5vrx_gui.py` is the repository-owned Windows desktop front end. Keep
  it aligned with the documented command-line workflow rather than introducing
  a separate build configuration.
- Its production build must use the pinned `espressif/idf:v6.0.2` Docker image
  and write artifacts into this repository's ignored `build/` directory. The
  application image is `build/c5vrx3.bin`.
- The GUI's **current validated build** flash mode intentionally writes the
  bootloader, partition table, and application through `tools/flash.py`.
- The GUI's collaborator `.bin` mode is application-only: write a known C5VRX
  ESP32-C5 app image at `0x10000`, preserving the board's bootloader and
  partition table. Do not treat a merged flash image or firmware for another
  target as an application image.
- Keep `tools/requirements-gui.txt`, `tools/Launch C5VRX GUI.bat`, and the
  README GUI documentation in sync with any GUI or flashing change. The batch
  launcher is the no-command entry point for Windows users.
