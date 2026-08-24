# ESP32-C5 continuous RF stream investigation

Status: **a hardware-only continuous-stream candidate is implemented but not
yet physically proven**. The C5 has an internal, hardware-written RF/FE dump
ring. The public API still exposes only a bounded diagnostic, but the recovered
producer register sequence can now feed a documented circular
GDMA/BitScrambler/PARLIO consumer without CPU or USB sample transport.

This is a negative engineering result, not proof that the silicon is incapable.
It defines one focused physical experiment which can falsify or advance the
remaining ring-reader hypothesis without pretending that repeated finite dumps
are continuous.

## Evidence boundary

The static results below are reproducible against ESP-IDF v6.0.2 with:

```bash
. "$IDF_PATH/export.sh"
python tools/audit_rf_streamability.py --idf "$IDF_PATH" --out audit.md
```

The audited `librftest.a` SHA-256 is
`0f9680b41612762d854accf2334412e3e01b206a3ec290bbbb232842fdaec7ba`.
The audit tests machine-code facts instead of trusting symbol names.

### Recovered C5 implementation

Disassembly of `adctrig` establishes all of the following for this blob:

- packed phase-bearing I/Q is written at fixed RAM base `0x40830000`;
- the reported allocation is `0x10000` bytes (16,384 32-bit words);
- `0x600a9004[16:0]` receives the capture length and bit 18 is polled;
- the low 16 bits of `0x600a9008` are returned as a current pointer;
- the historical `sample_80m` write is overwritten before enable and does not
  prove any physical rate;
- the wrapper polls for at most 1,000,000 microseconds;
- ordinary mode 0 pulses a software-trigger bit after enable and therefore does
  not require a decoded Wi-Fi packet trigger;
- mode 12 calls `ble_rx_start(0, 0)` and is not a lower-rate 5.8 GHz candidate;
- it clears the capture-enable bit before it returns;
- it creates no DMA descriptors and has no GDMA call relocation;
- `set_dump_mode` changes FE/AGC registers at `0x600a08cc` and
  `0x600a70b8`;
- exported `fedump_rd_rxmem` and `fedump_rd_txmem` are two-byte return stubs,
  not hidden stream readers.

Historical public Espressif RF-test tooling independently describes the same
`adctrig` arguments, a `curr_ptr`, `wrap_flag`, buffer address/size and circular
reordering after wrap. That corroborates a pre-trigger ring, but is not an
ESP32-C5 ABI guarantee and does not prove application code can drain it.

### Public-interface survey

| Candidate | What it exposes | Why it is not the target stream |
|---|---|---|
| Wi-Fi promiscuous callback | 802.11 packets and RX metadata | packet/PHY output, not the continuous waveform |
| Wi-Fi CSI | I/Q channel estimates for packet training fields/subcarriers | packet-triggered channel response, not time-domain RF samples |
| SAR ADC continuous driver | DMA samples from ADC-capable GPIO channels | the documented SAR ADC is not the Wi-Fi RF ADC |
| RF certification RX API | receive counts, errors and RSSI | no phase samples |
| RF/FE `adctrig` dump | packed phase-bearing words in a 64 KiB ring | wrapper is finite and disables capture on return |
| `fedump_rd_rxmem` | symbol only | C5 implementation is an empty stub |
| C5 GDMA | SPI2, UHCI, I2S0, AES, SHA, SAR ADC and PARLIO triggers | no Wi-Fi/modem/FE producer trigger |
| C5 BitScrambler | same documented peripheral attachment family | cannot attach directly to Wi-Fi/FE dump writer |
| CSI/FFT/baseband debug modes | finite or packet-derived diagnostic data | no public sustained phase-bearing producer contract |

Primary references:

- [ESP32-C5 Technical Reference Manual](https://documentation.espressif.com/esp32-c5_technical_reference_manual_en.pdf)
- [ESP32-C5 datasheet](https://documentation.espressif.com/esp32-c5_datasheet_en.html)
- [ESP-IDF Wi-Fi and CSI guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c5/api-guides/wifi.html)
- [ESP-IDF SAR ADC continuous driver](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c5/api-reference/peripherals/adc/adc_continuous.html)
- [ESP-IDF BitScrambler driver](https://docs.espressif.com/projects/esp-idf/en/latest/esp32c5/api-reference/peripherals/bitscrambler.html)
- [Espressif RF test guide](https://docs.espressif.com/projects/esp-test-tools/en/latest/esp32c5/development_stage/rf_test_guide/rf_test_guide.html)
- [Espressif RF calibration/certification receive API](https://docs.espressif.com/projects/esp-idf/en/latest/esp32c5/api-guides/RF_calibration.html)
- [public historical RF-test `adc_dump.py`](https://github.com/qiuzhi12345/eagletest/blob/19c5ddacddeb53ff245f65f206a3bfd7106e03cf/eagletest/py_script/rftest/rflib/adc_dump.py)

No public source, header, example, ROM symbol implementation, RF-test utility
or documentation found in this survey provides a C5 SDR API or a supported
Wi-Fi-RF-to-GDMA route.

## Direct circular RF-to-AV candidate

ESP-IDF v6.0.2 does document and implement the missing *consumer* half:

- PARLIO TX supports loop transmission using a GDMA link that returns to its
  start;
- a TX BitScrambler can attach inline to PARLIO;
- the BitScrambler may consume more input bytes than it emits, so the C5VRX
  program consumes four packed 32-bit I/Q words per eight-bit output item;
- at the measured design target of roughly 80 MS/s in and 20 MS/s out, PARLIO
  can clock those output items directly to the six-bit resistor DAC.

C5VRX therefore pre-arms a circular GDMA read of the complete 64 KiB RF ring,
starts the RF writer from LP RAM, waits until the writer is half a ring ahead,
then enables the PARLIO clock. The BitScrambler performs coarse phase lookup,
modulo phase subtraction (WBFM), 4:1 rate conversion and an initial three-times
video-deviation scale. No intermediate AV framebuffer is allocated.

The `AV DIRECT PROBE` command runs this overlap for 100 ms, then reports writer
advance, pointer changes, wraps, inferred source rate, guard canaries and full
register restoration. Passing requires a 70--90 MS/s measured writer rate and
multiple wraps; the result remains
`GAPLESS_RATE_CANDIDATE_AV_LOCK_PHYSICAL_TEST_REQUIRED` until a real VTX and AV
decoder lock to the output. Failure restores the standards-correct PAL logo and
makes no gapless claim.

Primary consumer-path sources:

- [ESP-IDF v6.0.2 PARLIO BitScrambler integration](https://github.com/espressif/esp-idf/blob/v6.0.2/components/esp_driver_parlio/src/parlio_bitscrambler.c)
- [ESP-IDF v6.0.2 PARLIO TX loop API](https://github.com/espressif/esp-idf/blob/v6.0.2/components/esp_driver_parlio/include/driver/parlio_tx.h)
- [ESP32-C5 BitScrambler peripheral attachment selectors](https://github.com/espressif/esp-idf/blob/v6.0.2/components/esp_hal_dma/esp32c5/include/hal/bitscrambler_peri_select.h)

## Producer-level follow-up

The wrapper boundary is no longer the end of the static investigation. See
[`rf-dump-producer.md`](rf-dump-producer.md) for the exhaustive C5 register
xref, the recovered 13-way trigger table, the exact eight hardware encodings
behind the misleading `sample_80m` argument, and a configure/start/observe/stop
split derived from vendor basic blocks.

## Ring experiment after producer modes

`RING_PROBE` starts **one** vendor `adctrig()` call in RX-error pre-trigger
mode, then observes the recovered write-pointer register and safely lagged dump
RAM while that single hardware arm is active. It never loops `adctrig`.

Run it first with no transmitter, then with an A4/5805 MHz source. Preserve the
full USB log. A ring-reader experiment is justified only if all are observed:

1. the call remains armed near its recovered one-second timeout;
2. the pointer changes repeatedly and stays within the 16,384-word ring;
3. dump RAM changes while the pointer advances;
4. captured I/Q changes coherently with injected RF;
5. measured pointer cadence agrees with the claimed sample mode.

Failure of items 1--3 rejects the accessible circular-ring hypothesis for the
tested silicon/blob. Items 4--5 prevent bus noise or a misidentified pointer
from being promoted to a source.

Passing the probe still does **not** prove a sustainable receiver. The guarded
reader is now **IMPLEMENTED / NOT PHYSICALLY TESTED**: it stays behind the
writer, copies only short contiguous windows into owned buffers, rejects
ambiguous copies, and records laps, overruns, missed words and drops through the
existing `c5vrx_rf_source_t` ABI. It remains explicitly experimental until
pointer cadence, coherency, safe distance and phase continuity are measured.

## Throughput feasibility

The recovered format is four bytes per complex sample. In the 80 MS/s design
case the
writer rate is therefore 320 MB/s and a 64 KiB ring wraps in about 204.8 us.
That makes a CPU copy-and-process loop implausible without measurement, and is
why the missing direct FE-to-DMA attachment matters. Decimating later does not
reduce ingress bandwidth or prevent overwrite.

The downstream path remains useful if a lower-rate phase-bearing tap or
undocumented DMA route is found: RF producer ABI, bounded buffering,
BitScrambler 4:1 WBFM, sample conditioner, scanline renderer and PARLIO/CVBS
sink remain modular. Software OSD insertion on recovered live video still
requires measured sync polarity/levels; the proof renderer demonstrates OSD
primitives without claiming recovered-video lock.

## Verdict

**DIRECT CONTINUOUS CANDIDATE IMPLEMENTED / PHYSICAL PASS PENDING.** There is no
public ESP32-C5 SDR API and the RF producer remains an audited, pinned internal
interface. The direct consumer no longer depends on a CPU copy loop: it uses
the documented circular PARLIO GDMA and inline BitScrambler path. C5VRX must
still not claim live analog FPV reception until the physical rate/wrap probe,
simultaneous ring arbitration and VTX-to-AV lock pass on the target XIAO.

The unresolved question remains:

> Can the ESP32-C5 provide a sufficiently continuous, phase-bearing RF stream
> for real-time WBFM demodulation?

Final producer pass: the historical `sample_80m` write is overwritten by the
later overlapping mode selector before capture enable. Eight distinct active
rates are therefore not established statically. Firmware now measures the
unchanged vendor argument paths and fails closed rather than forcing that
field. The next milestone is physical `AV DIRECT PROBE` measurement followed by
a scope/decoder check of discriminator polarity, gain and loaded DAC voltage.

For the focused tap, processing, mode and WBFM-bandwidth answer, see
[`rf-iq-dump-verdict.md`](rf-iq-dump-verdict.md).
