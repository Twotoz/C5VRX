# C5VRX roadmap

## Goal

A practical, open RX5808-class alternative based on inexpensive, currently obtainable ESP32-C5 hardware.

## Milestones

- [x] Identify exact FPV/Wi-Fi frequency overlap for Band A1-A7.
- [x] Add complete classic A/B/E/F/R FPV channel database.
- [x] Define realistic direct C5 target window: 5645-5885 MHz.
- [x] Add nearest-Wi-Fi-center planning for every FPV channel.
- [x] Use the supported ESP-IDF Wi-Fi driver as the default 5 GHz RF bring-up path.
- [x] Add a minimal RF certification/test research backend without pretending its 1-14 channel API is a 5 GHz tuner.
- [x] Add automated symbol/disassembly extraction tooling and CI artifacts.
- [x] Add symbol-size + relocation call-graph + call-site analysis for dump/frequency targets.
- [x] Correct `phy_set_freq` to the two-argument C5 ABI shape recovered from disassembly.
- [x] Add opt-in arbitrary-frequency experiment (`phy_set_freq(freq_mhz, offset)`).
- [x] Recover the C5 vendor finite ADC-dump format: signed 10-bit Q in bits 0-9 and signed 10-bit I in bits 10-19.
- [x] Recover the C5 dump RAM: 0x40830000, 64 KiB, up to 16384 complex samples.
- [x] Recover the 9-argument `adctrig` call shape and software-trigger mode from C5 + historical Espressif tooling.
- [x] Add opt-in finite ADC/IQ capture firmware and host decoder.
- [x] Add offline WBFM discriminator + synthetic DSP self-test.
- [x] Build ESP32-C5 firmware successfully in CI with ESP-IDF v6.0.2.
- [ ] Run the finite ADC/IQ capture on a real ESP32-C5.
- [ ] Verify exact-center A4 / 5805 MHz capture with VTX off/on.
- [ ] Confirm the finite capture contains the analog FPV WBFM carrier/modulation rather than an internal loopback-only signal.
- [ ] Feed the first real C5 I/Q dump into `tools/wbfm_demod.py` and recover analog-video baseband structure.
- [ ] Verify PAL/NTSC sync timing in the demodulated finite capture.
- [ ] Verify R5 / 5806 MHz reception from the ch161 HT40 bootstrap path.
- [ ] Validate `phy_set_freq(5806, 0)` shifts the real receiver center by +1 MHz.
- [ ] Characterize capture sample rate and effective receive bandwidth on hardware.
- [ ] Determine whether finite dump captures can be chained with acceptably small gaps.
- [ ] Trace the producer feeding dump RAM for continuous/ring-buffer capture.
- [ ] Implement real-time WBFM discriminator.
- [ ] Recover monochrome PAL/NTSC video.
- [ ] Recover color.
- [ ] Feed a display pipeline with measured end-to-end latency.
- [ ] Add RSSI / autoscan / runtime channel selection.
- [ ] Design a minimal C5VRX PCB if the silicon path proves viable.

## Current proof boundary

Static analysis is now strong enough to say the ESP32-C5 vendor RF-test code contains a finite complex I/Q dump path. It is **not** yet strong enough to say C5VRX receives analog FPV on hardware. The next decisive milestone is a physical A4/5805 capture showing source-dependent complex samples and recoverable WBFM baseband.

## Kill criteria

The direct C5-only path should be abandoned if the recovered finite I/Q dump cannot be driven from the live 5 GHz receive path, if its effective bandwidth is too narrow for analog FPV, or if there is no practical route from finite vendor RAM to continuous/chained phase-bearing samples.

Fallback architecture remains: use the C5 as a 5 GHz synthesizer/LO with a tiny external mixer/IF detector. That still avoids obsolete RX5808 silicon but is a separate hardware path.
