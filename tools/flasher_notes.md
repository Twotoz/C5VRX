# C5VRX one-click flasher

The Windows flasher is built from `tools/C5VRX_Flasher.py` and bundles a preconfigured A4 / 5805 MHz proof-test image. The GUI enumerates serial ports and flashes the selected ESP32-C5 automatically.

After flashing, run `tools/c5vrx_bench.py` as described in
`research/a4-bench-test.md`. It records the physical BitScrambler self-test,
VTX-off/on raw captures, finite RF-to-WBFM proof and chained-capture diagnostic
in one machine-readable report.
