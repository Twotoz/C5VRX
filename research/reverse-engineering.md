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
2. Normal 5 GHz Wi-Fi channel centers overlap seven analog FPV Band A channels exactly:
   - ch149 = 5745 MHz = A7
   - ch153 = 5765 MHz = A6
   - ch157 = 5785 MHz = A5
   - ch161 = 5805 MHz = A4
   - ch165 = 5825 MHz = A3
   - ch169 = 5845 MHz = A2
   - ch173 = 5865 MHz = A1
3. ESP-IDF can link Espressif's RF certification library by enabling `CONFIG_ESP_PHY_ENABLE_CERT_TEST`.
4. The public certification layer exposes RX start/stop and HT20/HT40 selection, but only returns packet-oriented counters/RSSI. This is not enough for analog video.
5. Much more interesting receive/debug machinery exists below that public API.

## New high-value finding

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

The C5 ROM symbol table separately exposes:

- `phy_chan_dump_cfg_752`
- `phy_adc_rate_set`
- `phy_fe_adc_on`
- `phy_iq_est_enable` / `phy_iq_est_disable`
- `phy_dc_iq_est`
- `phy_fft_scale_force`
- `phy_set_rx_pbus_freq`
- `phy_write_chan_freq`

This changes the research target. We no longer have to begin by inventing an ADC path from scratch; we should first determine what the existing **FE dump / channel dump** machinery captures and how its RX memory is formatted.

## Working hypothesis

The names `set_dump_mode`, `loop_dump_test`, `print_dump_data` and `fedump_rd_rxmem` likely belong to a factory/ATE receive-dump facility. The strongest possibility is a finite capture of front-end/baseband samples into internal memory for RF calibration/debug.

That is not yet proof of raw I/Q. The buffer could hold FFT bins, calibration statistics, channel estimates, decimated samples, or another internal format. We must establish this experimentally.

## Reverse-engineering order

### Phase 0 — safe baseline

Build and flash the normal C5VRX firmware. Start on 5805 MHz / Wi-Fi channel 161. Verify the C5 can enter its RF-test RX state without crashing.

### Phase 1 — static binary analysis

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

### Phase 2 — identify RX dump memory

Find the address and size consumed by `fedump_rd_rxmem` / `print_dump_data`. Determine whether data changes with:

1. no RF source,
2. unmodulated 5.8 GHz carrier,
3. analog FPV transmitter showing a static black frame,
4. black/white checkerboard,
5. moving video.

A useful capture should vary continuously with the analog source even though no valid 802.11 packet exists.

### Phase 3 — classify sample format

Candidate formats:

- interleaved signed I/Q,
- separate I and Q banks,
- complex FFT output,
- magnitude-only FFT,
- correlation/IQ-estimator accumulators,
- post-filter real samples.

For raw complex samples, instantaneous FM can be recovered with a low-cost discriminator such as:

```text
z[n] = I[n] + jQ[n]
dphi[n] = arg(z[n] * conj(z[n-1]))
```

For small phase steps, an atan-free approximation can later be used on-device.

### Phase 4 — continuous capture

A finite factory dump is enough to prove the concept but not enough for live FPV. After proving sample content, trace how the dump writer is fed. The real target is the producer before the finite debug RAM, then redirect/copy it into a DMA/ring-buffer path.

### Phase 5 — WBFM to composite

Once continuous I/Q is available:

1. DC removal / optional IQ correction
2. WBFM phase discriminator
3. low-pass roughly covering analog-video baseband
4. level normalization
5. PAL/NTSC sync/color handling
6. output either decoded pixels or a reconstructed composite stream

Latency matters more than perfect image quality. Avoid full-frame buffering until the receive chain is proven.

## Important caution

Do not call undocumented vendor functions purely from guessed prototypes. On RISC-V a wrong argument count/type can silently corrupt state or crash the RF subsystem. Establish each prototype from disassembly/call sites first and gate experimental calls behind a compile-time option.
