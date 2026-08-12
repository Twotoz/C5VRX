# C5VRX roadmap

## Goal

A practical, open RX5808-class alternative based on inexpensive, currently obtainable ESP32-C5 hardware.

## Milestones

- [x] Identify exact FPV/Wi-Fi frequency overlap for Band A1-A7.
- [x] Add complete classic A/B/E/F/R FPV channel database.
- [x] Define realistic direct C5 target window: 5645-5885 MHz.
- [x] Add nearest-Wi-Fi-center planning for every FPV channel.
- [x] Confirm RF certification libraries are linkable from ESP-IDF.
- [x] Identify promising C5 RF-test symbols for ADC/IQ/front-end dump research.
- [x] Add a minimal C5 RF-test RX harness.
- [x] Add automated symbol/disassembly extraction tooling.
- [x] Add symbol-size + relocation call-graph analysis for dump/frequency targets.
- [x] Identify `phy_set_freq(int freq_mhz)` as an arbitrary-frequency candidate hook.
- [x] Add opt-in weak-symbol direct-frequency experiment.
- [x] Add menuconfig-selectable A/B/E/F/R target channel and experimental tuning switch.
- [x] Add offline WBFM discriminator + synthetic DSP self-test.
- [x] Document historical FE-dump evidence and reprioritize `loop_dump_test` analysis.
- [x] Add ESP-IDF v6.0.2 CI build definition for ESP32-C5.
- [ ] Make the ESP-IDF CI build pass on `main`.
- [ ] Build on a real ESP32-C5 board.
- [ ] Verify 5 GHz RF-test RX on channel 161 / 5805 MHz.
- [ ] Verify R5 / 5806 MHz reception from the ch161 HT40 bootstrap path.
- [ ] Validate whether `phy_set_freq(5806)` actually shifts the C5 receiver center by +1 MHz.
- [ ] Recover C5 calling convention for `set_dump_mode` and `loop_dump_test`.
- [ ] Locate/parse the receive dump buffer.
- [ ] Prove the dump changes in response to a non-802.11 analog FPV carrier.
- [ ] Determine whether captured data preserves phase (raw/near-raw IQ).
- [ ] Feed first real capture into `tools/wbfm_demod.py`.
- [ ] Recover a short analog-video waveform offline.
- [ ] Obtain continuous or sufficiently chunked sample capture.
- [ ] Implement real-time WBFM discriminator.
- [ ] Recover monochrome PAL/NTSC video.
- [ ] Recover color.
- [ ] Feed a display pipeline with measured end-to-end latency.
- [ ] Add RSSI / autoscan / runtime channel selection.
- [ ] Design a minimal C5VRX PCB if the silicon path proves viable.

## Kill criteria

The direct C5-only path should be abandoned if all available FE/channel-dump paths are magnitude/statistics-only or if there is no practical way to obtain continuous phase-bearing samples at sufficient bandwidth.

Fallback architecture remains: use the C5 as a 5 GHz synthesizer/LO with a tiny external mixer/IF detector. That still avoids obsolete RX5808 silicon but is a separate hardware path.
