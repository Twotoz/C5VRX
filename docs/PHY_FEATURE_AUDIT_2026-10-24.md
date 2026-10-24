# ESP32-C5 PHY feature audit, 2026-10-24

C5VRX by Twotoz and contributors. [Source](https://github.com/Twotoz/C5VRX),
[official website and Discord invite](https://twotoz.github.io/C5VRX/).
Extends the project's pinned-library investigation in
`legacy/c5vrx4/main/phy_rx_lab.c`, `legacy/c5vrx4/PREDEMOD_LAB.md`,
`legacy/c5vrx3/docs/pre-native-noise-audit.md` and current `docs/FINDINGS.md`.

The operator requested a wider audit after the first PR254 measurements,
with only the USB-connected C5 available and the VTX remaining off. This
document distinguishes reversible new board probes from existing results,
unverified controls and proposals that require a carrier.

## Crucial correction to the first experiment's interpretation

`phy_iq_corr_enable()` enables both `600A0438[31:29]` and
`600A0C0C[14:13]`. The same pinned archive's `phy_rxiq_set_reg()` writes
RX coefficients at `600A0438[28:16]`, whereas `phy_txiq_set_reg()` writes
TX coefficients at `600A0C0C[12:0]`. Therefore the first `IQ_CORR_OFF`
profile is a **combined RX/TX enable experiment**, not a clean RX-IQ-only
experiment. Its approximately +0.8..1.0 dB captured-noise difference cannot
all be attributed to RX IQ calibration, noise figure or preserved video.

Even an isolated lower noise variance does not establish better analog FM.
An I/Q transform changes noise covariance and can also change desired-carrier
amplitude, image rejection, phase linearity and WBFM sidebands. Noise-only
snapshots do not establish the sign of the resulting video-quality change.
Individual enable-bit functions remain undocumented; labels below describe
the exact write rather than assigning guessed DSP semantics.

## New isolated suite H/V

H uses 300 ms dwell, V 1200 ms. Both reuse the same gain screening, whole-suite
PHY lease, per-capture readback, contiguous IQ capture, saved-mask rollback,
abort rules and nonpersistent execution as B/C. No production default changes.
USB hex output is paced at two RTOS ticks per line to reduce transport loss;
all captures still require byte count, ordering and checksum verification.

| Candidate | Recovered write | Experiment / limit |
|---|---|---|
| RX correction enables | `600A0438[29]`, `[30]`, `[31]` | Clear each individually and all three together; calibrated coefficients preserved |
| TX correction enables | `600A0C0C[13]`, `[14]` | Clear each individually and both together; determine whether the earlier combined result originated in the RX bank |
| RX coefficient A | `600A0438[28:22]` | Saved calibrated field versus raw zero; not called an identity transform |
| RX coefficient B | `600A0438[21:16]` | Same, separately and with A; both full saved fields restored |
| PKDADC | `600A0C38[31]` | Clear only recovered enable; retain all thresholds/secondary controls |
| BB CCA | `600A7018[30]` | Clear only the established isolated lab bit; retain threshold |
| MAC CCA | `600A4C5C[31:28]` | Recovered enable/disable tuples 0/A, both two-bit fields, saved-mask rollback |
| Digital filter | `600A0430[21:18]` | Compare known-working modes 4/8/12 with production 0 at both frequencies; retain ADC selector and analog calibration |
| FFT force | `600A7800[19]`, `[27:20]` | Force disabled versus force-on with scale field zero; no arbitrary scale or OFDM-helper argument guessing |

`phy_rxiq_scale_set()` remains read-only: selector `phy_param[650]` chooses
byte-field values in `600A043C`; neither an equivalent dB scale nor benefit to
MODEM_DIAG has been established. An amplitude reduction is not noise removal.

## Other avenues and present boundaries

| Area | Evidence / next action |
|---|---|
| Analog filter/calibrated RC capacitors | Already measured extensively by C5VRX and ESPARGOS lineage. Keep per-chip values and BW20/BW40 branch distinction; do not relabel old work as new. 11p remains the measured narrower-band candidate. |
| ADC selector | The 5830 MHz boundary and coupled analog-I2C field are known from the prior lab. A selector override is not independent of converter/capture timing; prior work and field/rate readback must be preserved. Do not mistake repeated/stalled samples for lower noise. |
| Spur | Pinned production and RF-test callers use slot0; it is off on this board. The adjacent raw word has bit13 set, but that is not sufficient proof of a valid active slot1. Other slots remain read-only pending stronger bounds/semantics and coefficient-state evidence. |
| Packet sensing, 11b optimizations, LLTF masks | Names and Wi-Fi packet benefits do not establish relevance to continuous MODEM_DIAG. Isolate only fully recovered reversible masks, rather than calling compound helpers with guessed arguments. |
| Native AGC | Earlier hardware showed periodic reacquisition/line noise. It is not silently restored in a noise experiment; current Direct Gain remains the owner. |
| RF calibration policy | Effective configuration uses partial RF calibration with NVS storage. Espressif documents that this reuses stored full-calibration data; full calibration is a boot-time comparison candidate, not a safe in-place helper toggle or proof the existing calibration is wrong. Preserve calibration provenance and avoid erasing NVS. |
| PLL/temperature tracking | Explicitly disabled in the effective config; do not assume a dormant tracking loop is a hidden 1 dB loss. Reinitializing or recalibrating PLL/RC requires complete boot/restore analysis. |
| USB and DAC self-interference | Existing console N compares live output on/off. It should be repeated at a non-railing fixed gain; USB disconnect cannot be used as a noise measurement without an autonomous capture/retrieval protocol. |
| Clock/power/coexistence | PM, disconnected-station PM and software coexistence are disabled. Do not manipulate undocumented clocks or supply/bias currents under a noise-suppressor label. The live PARLIO clock and continuous source remain invariant. |
| Antenna diversity | Espressif supports an external RF switch; no second RF path/switch exists in the stated USB-only setup. No software-only diversity gain can be measured here. |

Primary vendor references consulted:

- [Espressif C5 RF calibration guide](https://docs.espressif.com/projects/esp-idf/en/latest/esp32c5/api-guides/RF_calibration.html).
- [Espressif C5 datasheet](https://documentation.espressif.com/esp32-c5_datasheet_en.html)
  for receiver/filter/DC hardware and external antenna-diversity requirements.
- Exact IDF 6.0.2 `libphy.a`, SHA-256
  `dbf33c418c8d408d4005c849d12a1432deea82e2e5e57de3c8ddf914d104fffb`.
  Local full symbol/disassembly and selected symbol excerpts are retained beside
  the raw data; they are static evidence, not live functional specifications.

New measured results and their quality flags are recorded separately from this
static inventory. No additional dB or range gain is claimed by adding a probe.

## Second measurement refinement

The initial audit's signed-rail flags motivated a stronger gain screen:
zero observed signed rails, `abs(mean)+6*sigma <= 6 cells` per axis,
m2 above 0.67 cells² and four valid screening captures. This trades gain
and relative quantization error for enough headroom to test correction/filter
states without routinely folding the sign-preserving slice. It does not
guarantee that later interference cannot corrupt a capture.

Console E now repeats video output ON→OFF→OFF→ON three times at this fixed
screened gain, verifying output stop/resume through the existing transport.
A failed resume reboots before releasing the observer. This changes no saved
output level or filter profile. H/V/B/C remain available separately.

[Extended board results](PHY_AUDIT_BENCH_2026-10-24.md) retain the initial
flagged data as well as the more conservative repeats. The machine-readable
[503-symbol inventory](../research/data/esp_phy_inventory_2026-10-24.json)
contains all defined code symbols, name-based triage, mention in application
source and presence in the measured ELF. These fields are search aids, not
evidence of execution or of a correctly decoded ABI.

## Coupled ADC selector and gain-table follow-up

The complete pinned `phy_adc_rate_set(selector)` has only two hardware
effects: analog I2C block66/host0/reg4[2]=!selector and
`600A0448[1:0]`=selector duplicated. Console S compares selectors0/1 with
the incoming stock state, restoring only these saved bits. Every capture
checks both domains. Neither a filter helper nor PLL/calibration is called.
The test records both hardware views and the unchanged dfilt setting.

The new read-only gain-tuple record uses the existing hardware-matched
reconstruction. The measured rows G73/G74 use stage8/RF code487, with the
highest RF stage already beginning at G54 and max index83. A hypothetical
low-LNA/high-BB allocation error is not present in those listening states.

The ADC repeat deltas and no-op controls are in the extended board report.
The initial −0.14 dB on 5865 MHz does not persist clearly over controls;
no production selector override was added. The full-RF-calibration boot
comparison is separately archived in
[PHY_CALIBRATION_BENCH_2026-10-24.md](PHY_CALIBRATION_BENCH_2026-10-24.md).
