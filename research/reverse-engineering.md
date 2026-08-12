# Reverse-engineering notes

## Mission

C5VRX investigates whether an ESP32-C5 can be repurposed as a low-cost analog 5.8 GHz FPV receiver, primarily as an RX5808/RTC6715-class alternative during the current receiver-module shortage.

The desired end state is:

```text
5.8 GHz analog FPV WBFM
        |
        v
ESP32-C5 RF front-end
        |
        v
raw/near-raw I/Q or equivalent receive samples
        |
        v
WBFM discriminator
        |
        v
PAL/NTSC composite baseband
        |
        +--> video decoder / display pipeline
```

## What is established

1. ESP32-C5 has a real 5 GHz receive chain. The chip internally converts RF to quadrature baseband and digitizes the receive path before Wi-Fi baseband processing.
2. Normal 5 GHz Wi-Fi channel centers overlap seven analog FPV Band A channels exactly, but C5VRX is **not limited to Band A**. The firmware now carries the classic A/B/E/F/R tables and plans every frequency against the nearest 5 GHz Wi-Fi center.
3. The current direct hardware target is **5645-5885 MHz**. Legacy FPV channels above 5885 MHz are intentionally marked out-of-window.
4. ESP-IDF can link Espressif's RF certification library by enabling `CONFIG_ESP_PHY_ENABLE_CERT_TEST`.
5. The public certification layer exposes RX start/stop and HT20/HT40 selection, but only returns packet-oriented counters/RSSI. This is not enough for analog video.
6. Much more interesting receive/debug machinery exists below that public API.

## High-value finding: arbitrary frequency control

The current ESP32-C5 PHY symbol inventory includes several frequency-control functions:

```text
phy_set_freq
phy_set_chanfreq
phy_set_rfpll_freq
phy_set_channel_rfpll_freq_new
phy_write_chan_freq
phy_freq_to_chan
phy_chan_to_freq
phy_chip_set_chan
phy_chip_set_chan_ana
phy_chip_set_chan_offset
```

A separate open reverse-engineering project, `esp-hosted-open`, declares and calls the candidate hook as:

```c
extern void phy_set_freq(int freq_mhz) __attribute__((weak));
```

C5VRX now includes the same weak-symbol experiment behind an opt-in compile-time switch. This is a strong lead, but it is **not yet a verified vendor ABI**. The first validation target is RaceBand 5 at **5806 MHz**, only +1 MHz from the known standard Wi-Fi center at 5805 MHz.

## High-value finding: receive/sample dump path

The C5 RF-test binary exposes symbol names strongly suggesting a front-end/sample dump path, including:

- `adctrig`
- `sampledeal`
- `accumiq`
- `get_rx_buffer`
- `get_rx_data_addr`
- `set_dump_mode`
- `print_dump_data`
- `loop_dump_test`
- `fedump_rd_rxmem`

The C5 PHY symbol inventory separately exposes:

- `phy_chan_dump_cfg` / `phy_chan_dump_cfg_752`
- `phy_adc_rate_set`
- `phy_fe_adc_on`
- `phy_iq_est_enable_new` / `phy_iq_est_disable`
- `phy_dc_iq_est_new`
- `phy_fft_scale_force`
- `phy_set_rx_pbus_freq`
- `phy_get_rx_pbus_freq`

This changes the research target. We no longer have to begin by inventing an ADC path from scratch; we should first determine what the existing **FE dump / channel dump** machinery captures and how its RX memory is formatted.

## Working hypothesis

The names `set_dump_mode`, `loop_dump_test`, `print_dump_data` and `fedump_rd_rxmem` likely belong to a factory/ATE receive-dump facility. The strongest possibility is a finite capture of front-end/baseband samples into internal memory for RF calibration/debug.

That is not yet proof of raw I/Q. The buffer could hold FFT bins, calibration statistics, channel estimates, decimated samples, or another internal format. We must establish this experimentally.

## Reverse-engineering order

### Phase 0 — safe baseline

Build and flash the normal C5VRX firmware. The current default target is R5 / **5806 MHz**, bootstrapped through Wi-Fi channel 161 / 5805 MHz in HT40. Direct retuning remains disabled by default.

### Phase 1 — frequency validation

Before depending on undocumented direct tuning:

1. Verify RF-test RX at 5805 MHz.
2. Verify a 5806 MHz source is visible from the 5805 MHz HT40 bootstrap path.
3. Enable the `phy_set_freq(5806)` experiment.
4. Confirm the receive center really shifts using a controlled narrowband source.
5. Repeat at larger offsets only after the +1 MHz test is repeatable.

See [`frequency-tuning.md`](frequency-tuning.md).

### Phase 2 — static binary analysis

Run:

```bash
python tools/analyze_phy.py
```

Focus on these call relationships:

```text
loop_dump_test
  -> set_dump_mode ?
  -> adctrig ?
  -> fedump_rd_rxmem ?
  -> print_dump_data ?

sampledeal
  -> accumiq ?
```

For every target establish:

- number of arguments from RISC-V register use (`a0`..`a7`)
- return value use
- constants written to MMIO
- referenced memory ranges
- calls into ROM symbols

### Phase 3 — identify RX dump memory

Find the address and size consumed by `fedump_rd_rxmem` / `print_dump_data`. Determine whether data changes with:

1. no RF source,
2. unmodulated 5.8 GHz carrier,
3. analog FPV transmitter showing a static black frame,
4. black/white checkerboard,
5. moving video.

A useful capture should vary continuously with the analog source even though no valid 802.11 packet exists.

### Phase 4 — classify sample format

Candidate formats:

- interleaved signed I/Q,
- separate I and Q banks,
- complex FFT output,
- magnitude-only FFT,
- correlation/IQ-estimator accumulators,
- post-filter real samples.

For raw complex samples, instantaneous FM can be recovered with:

```text
z[n] = I[n] + jQ[n]
dphi[n] = arg(z[n] * conj(z[n-1]))
```

`tools/wbfm_demod.py` now implements this offline and includes a synthetic self-test. See [`dsp-pipeline.md`](dsp-pipeline.md).

### Phase 5 — continuous capture

A finite factory dump is enough to prove the concept but not enough for live FPV. After proving sample content, trace how the dump writer is fed. The real target is the producer before the finite debug RAM, then redirect/copy it into a DMA/ring-buffer path.

### Phase 6 — WBFM to composite

Once continuous I/Q is available:

1. DC removal / optional IQ correction
2. WBFM phase discriminator
3. low-pass roughly covering analog-video baseband
4. level normalization
5. PAL/NTSC sync/color handling
6. output either decoded pixels or a reconstructed composite stream

Latency matters more than perfect image quality. Avoid full-frame buffering until the receive chain is proven.

## Important caution

Do not call undocumented vendor functions purely from guessed prototypes. The `phy_set_freq(int)` prototype has an independent public implementation using the same call shape, which makes it a better candidate than a name-only guess, but it still needs real C5 validation. Every other undocumented hook should be established from disassembly/call sites first and gated behind an explicit experimental option.
