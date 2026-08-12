# C5VRX roadmap

## Goal

A practical, open RX5808-class alternative based on inexpensive, currently obtainable ESP32-C5 hardware.

## Milestones

- [x] Identify exact FPV/Wi-Fi frequency overlap for Band A1-A7.
- [x] Confirm RF certification libraries are linkable from ESP-IDF.
- [x] Identify promising C5 RF-test symbols for ADC/IQ/front-end dump research.
- [x] Add a minimal C5 RF-test RX harness.
- [x] Add automated symbol/disassembly extraction tooling.
- [ ] Build on a real ESP32-C5 board.
- [ ] Verify 5 GHz RF-test RX on channel 161 / 5805 MHz.
- [ ] Recover calling convention for `set_dump_mode` and `loop_dump_test`.
- [ ] Locate/parse the receive dump buffer.
- [ ] Prove the dump changes in response to a non-802.11 analog FPV carrier.
- [ ] Determine whether captured data preserves phase (raw/near-raw IQ).
- [ ] Recover a short analog-video waveform offline.
- [ ] Obtain continuous or sufficiently chunked sample capture.
- [ ] Implement real-time WBFM discriminator.
- [ ] Recover monochrome PAL/NTSC video.
- [ ] Recover color.
- [ ] Feed a display pipeline with measured end-to-end latency.
- [ ] Investigate arbitrary frequency programming for the rest of FPV bands.
- [ ] Add RSSI / autoscan / channel table.
- [ ] Design a minimal C5VRX PCB if the silicon path proves viable.

## Kill criteria

The direct C5-only path should be abandoned if all available FE/channel-dump paths are magnitude/statistics-only or if there is no practical way to obtain continuous phase-bearing samples at sufficient bandwidth.

Fallback architecture remains: use the C5 as a 5 GHz synthesizer/LO with a tiny external mixer/IF detector. That still avoids obsolete RX5808 silicon but is a separate hardware path.
