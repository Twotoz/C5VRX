# C5VRX roadmap

## Goal

A practical, open RX5808-class alternative based on inexpensive, currently
obtainable ESP32-C5 hardware, with **normal analog CVBS as the primary product
output**.

The core architecture should stay streaming and minimal:

```text
5.8 GHz RF -> C5 receive path -> WBFM -> sampled CVBS -> passive DAC -> AV out
```

Digital LCD/USB video is deliberately outside the critical path.

## Architecture decisions

- [x] Make analog CVBS the primary output target.
- [x] Avoid full PAL/NTSC-to-pixel decoding in the core receiver.
- [x] Use PARLIO + GDMA as the reference sampled-video output engine.
- [x] Select a 6-bit passive DAC as the reference quality/BOM compromise.
- [x] Define a nominal ~75-ohm / ~1 Vpp passive output network for hardware validation.
- [x] Keep external video DAC, video decoder and OSD ICs out of the minimum design.
- [x] Treat one-GPIO/noise-shaped video as optional research, not the mainline architecture.

## RF / reverse-engineering milestones

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
- [ ] Confirm the finite capture contains live analog-FPV WBFM rather than an internal loopback-only signal.
- [ ] Feed the first real C5 I/Q dump into `tools/wbfm_demod.py` and recover composite baseband structure.
- [ ] Verify PAL/NTSC sync timing in the demodulated finite capture.
- [ ] Verify R5 / 5806 MHz reception from the ch161 HT40 bootstrap path.
- [ ] Validate `phy_set_freq(5806, 0)` shifts the real receiver center by +1 MHz.
- [ ] Characterize capture sample rate and effective receive bandwidth on hardware.
- [ ] Determine whether finite dump captures can be chained with acceptably small gaps.
- [ ] Trace the producer feeding dump RAM for continuous/ring-buffer capture.

## Analog output milestones

- [x] Add an independent PARLIO CVBS line-waveform experiment.
- [x] Implement a streamed PAL 625/50 interlaced raster without a full-frame framebuffer.
- [x] Add half-line equalizing/broad vertical sync and normal horizontal sync timing.
- [x] Add a grayscale test picture and optional 4.43361875 MHz swinging-burst stress signal.
- [x] Add a host-side golden PAL waveform/chunk-wrap self-test.
- [x] Add a dedicated output-only DevKit configuration using GPIO 0/1/6/8/9/10.
- [x] Add a CI-built PAL CVBS proof firmware artifact and wiring guide.
- [x] Correct the passive-DAC model to validate the source-matched 75-ohm reference network.
- [ ] Prove the streamed PAL raster is stable on physical ESP32-C5 hardware.
- [ ] Generate and validate a complete stable NTSC test raster.
- [ ] Build the reference 6-bit passive DAC on a real C5 board.
- [ ] Scope source impedance, sync level, blank level, black level and white level into a 75-ohm load.
- [ ] Compare 4-, 5- and 6-bit output on real analog monitors/goggles.
- [ ] Verify the PAL-frequency burst survives the physical GPIO/DAC/cable path.
- [ ] Decide whether a production output buffer is actually required or only optional for long cables/ESD robustness.

## Real-time DSP milestones

- [x] Model a 2 KiB I/Q-to-phase BitScrambler LUT.
- [x] Identify the one-LUT hardware constraint: the phase LUT and a second full 2 KiB delta LUT cannot be resident simultaneously.
- [ ] Implement the single resident phase-LUT on C5 BitScrambler hardware.
- [ ] Implement phase[n] - phase[n-1] using available state/counter/output-history mechanisms.
- [ ] Benchmark sustainable sample throughput on physical C5 hardware.
- [ ] Filter/decimate the discriminator output into a stable CVBS sample stream.
- [ ] Join continuous RF samples -> WBFM -> CVBS -> PARLIO.
- [ ] Measure end-to-end RF-to-CVBS latency.

## Receiver features after live video

- [ ] Add signal-power/RSSI metric.
- [ ] Add channel scanning and analog-video detection.
- [ ] Add runtime channel selection.
- [ ] Add simple sample-domain monochrome OSD only if useful.
- [ ] Design a minimal analog-first C5VRX PCB.

## Explicitly deferred

These are not blockers for C5VRX and should not consume mainline effort before
live analog CVBS works:

- full PAL/NTSC-to-RGB decoding;
- direct LCD rendering;
- full-frame buffering;
- UVC/high-speed USB video;
- HDMI/digital video output;
- companion-processor display pipelines.

## Current proof boundary

Static analysis is strong enough to say the ESP32-C5 vendor RF-test code
contains a finite complex I/Q dump path. It is **not** yet strong enough to say
C5VRX receives analog FPV on hardware.

The output side now has a complete software proof target: a CI-built,
output-only PAL raster generator plus a source-matched 6-bit passive DAC model.
That is still **not** a physical video proof until scope and monitor tests pass.

The next decisive RF milestone is a physical A4/5805 capture showing
source-dependent complex samples and recoverable WBFM baseband.

The next decisive output milestone is independent: prove a correctly scaled,
stable six-bit CVBS signal from PARLIO into a real 75-ohm video load.

These two halves can be developed independently and joined only after both are
measured.

## Kill criteria

Abandon the direct C5-only RF path if the recovered sample mechanism cannot be
driven from the live 5 GHz receive path, if usable bandwidth is too narrow for
analog FPV, or if no practical continuous/chained phase-bearing stream can be
recovered.

Fallback architecture remains using the C5 as a 5 GHz synthesizer/LO with a
tiny external mixer/IF detector. Even in that fallback, the output side should
remain analog-first: demodulated CVBS -> passive/low-component video output.
