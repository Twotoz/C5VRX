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
USB hex output initially paused two RTOS ticks per line. The current lab
uses 5 ms and bounded identity/hash ACK retries after rare transport losses;
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

## Additional bias and nominal noise-helper audit

The exact pinned `noise_check_loop` symbol is a single `ret` instruction;
its suggestive name supplies no hidden running noise-suppression loop.
`phy_bias_reg_set` has no input-dependent branches or selectable high/low
mode: it writes analog I2C block0x6a/host1, reg0[3:0]=15,
reg1[7:4]=7, reg0[7:4]=9 and reg1[3:0]=15. Its final bytes are 0x9f/0x7f.
The pinned `phy_rf_init` calls it during RF initialization. Neither the
numbers nor the symbol name establish current direction, LNA noise figure,
or a valid overdrive range. New stage telemetry reads both bytes without
changing them; stock values must be witnessed before proposing a bias test.

The board witnesses at 5865 and 5805 MHz read **0xcf/0x7f**, not the initializer's
0x9f/0x7f. The same pinned `phy_freq_start_cal_5g_code` writes precisely
reg0[7:4] during its calibration iteration, with restarts and loop-SNR
checks. Thus this field also belongs to the frequency-calibration process;
resetting it to the initializer is not justified as an LNA noise tweak.
The full selected-function disassembly is archived locally as
`bias-extra-evidence.txt` (SHA-256 recorded in the measurement summary).

`phy_agc_max_gain_set` changes several native-AGC fields, including an
unmasked shifted argument at 0x600a08bc. It is not a bounded knob for the
current forced-gain receiver. `phy_wifi_agc_sat_gain` writes full words at
0x600a7064/0x600a7114. No safe extra receive-gain tuple or continuous-FM
noise benefit follows from either function name, so neither is called with
guessed arguments. The existing hardware-verified gain table already uses
the highest documented-by-vendor-table RF stage at G54 and above.

## Further packet/RX policy audit

The complete pinned bodies of four helpers permit small isolated profiles:

| Helper | Full write footprint | Bench action |
|---|---|---|
| `phy_lltf_mask_en(a,b)` | 0x600a790c bits13/12 from boolean a/b | Test 00/10/01/11 independently of the other helpers |
| `phy_wifi_rifs_mode_en(a)` | 0x600a70f4 bit0 | Test 0/1; `phy_rx_rifs_en` is just its wrapper |
| `phy_vht_support(a)` | 0x600a7ce4 bit5 | Test 0/1, preserving adjacent 11p fields |
| `phy_rx11blr_cfg(a)` | 0x600a8060 bits10/11 and 0x600a807c bit11 | Test both coupled boolean states |

Suite9 uses a verified matching calibrated DC hold at its fixed screened
gain, separate ABBA profiles and a stock sham. These are tests of recovered
boolean writes, not claims about what the packet engine does to continuous
MODEM_DIAG. Desired-signal phase/amplitude and video are unmeasured.

Two nominal ADC-random helpers do not expose a reversible dither switch.
This reconfirms the earlier C5VRX-4 `PREDEMOD_LAB.md` RX-path audit;
it is not a newly discovered dither limitation.
`phy_get_adc_rand` is a single `ret`, with no hardware access. The enabled
branch of `phy_set_adc_rand` turns on RX/TX clocks, changes 0x600a0890 bit24,
enters PBUS debug, forces DCO/RX power and runs DC calibration. Its disabled
branch powers RX off and returns to PBUS work mode; it is not exact rollback
to the original listening state. Do not call this pair as a noise toggle.

`phy_rx_filter_mode` is the already-measured 0x600a0430[21:18] digital filter
field, rather than a separate new filter. `phy_pkdadc_set` owns additional
threshold fields beyond its already-tested enable: its full-word write at
0x600a7068 and unknown meaningful threshold settings prevent treating it as
one more boolean. `phy_bb_bss_cbw40_dig` clears both bits3:2 at 0x600a9c18
and sets bit2 from its argument; its higher wrapper also calls bandwidth
selection. Neither is an independently established noise filter.

The internal `phy_get_loop_snr` calibration routine changes RX/TX clocks,
PBUS mode and loopback gain, performs DC calibration, starts a TX tone and
estimates internal IQ SNR. Cleanup depends on its arguments. It does not
measure a fixed signal arriving at the antenna through the normal live
receiver, and is not invoked here as a substitute for calibrated weak RF.

## Adjacent LO-capacitor codes

The earlier C5VRX-4 RX-path audit identified block0x63/host1/reg21[4:0]
as the field set from `phy_get_dcap_degen(f)` by `phy_set_freq_i2c_new`.
The pinned code computes 10 + trunc((5880-f)/36) on the 5GHz branch and
clamps its result to 10..31. It predicts code10 at 5865 and code12 at 5805.
The whole frequency setter also writes the bias field's high nibble to
**12 above 4000 MHz**, directly explaining operating 0xcf versus the
initializer's 0x9f; it also changes crystal/VCO fields. None of those other
frequency-setter writes is included in the cap experiment.

Console `)` tests the incoming cap code as a sham, then its adjacent -1/+1
codes only within 10..31, preserving the byte's upper three bits. It uses
16 fresh raw snapshots per ABBA stage at a fixed screened gain with a
matching calibrated DC hold. Per-capture readback checks the cap field,
held DC pair and actual RF/BB/fine tuple. The original code is restored
and verified even on early abort; a failed restore reboots before resume.
An out-of-interval incoming code refuses the experiment.

This tests a bounded neighbouring setting rather than assuming the vendor
formula is optimal for this chip. Code magnitude is not a calibrated
capacitance or LNA-noise setting. A variance change could reflect altered
LO conversion gain or distortion; no RF sensitivity claim is possible
without a desired signal. The existing live tuning/defaults remain intact.

## Configuration and automatic tracking boundary

The current pinned SDK configuration disables power management and software
coexistence; `CONFIG_ESP_COEX_ENABLED=y` still denotes coexistence component
availability, not proof that software coexistence arbitration is active.
The CPU default is240 MHz. Neither a component name nor an absent second
radio proves a hidden sensitivity improvement from changing clocks.

The SDK USB-PHY workaround and receive-11b power-supply workaround depend
on target capability symbols that are not defined in this C5 configuration.
Their menu descriptions cannot be applied to the C5 merely because they
mention improved Wi-Fi reception. Certification-test support adds callback
source and RF-test archives to the link inputs. Current map inspection
finds no RF-test archive member included in executable code; enabling API
availability is not evidence that a certification transmitter is running.

Automatic PLL tracking is explicitly disabled. The pinned IDF
`phy_common.c` timer would acquire the SDK PHY lock and call
`phy_param_track_tot`, outside C5VRX's application PHY lease. The recovered
temperature-triggered `phy_cal_param_track` can force TX/RX off, clear PBUS,
recalibrate DC/IQ and regenerate the gain table before retuning. This is
the same maintenance path already identified by the C5VRX-4 tracking lab;
it is not an isolated PLL-noise bit. Simply enabling the timer during an
ABBA suite would violate its fixed-state/calibration ownership guarantees.
No periodic-tracking noise or range benefit is claimed, and the disabled
production setting is retained. A coordinated boot/maintenance experiment
would first need validated gain-table/DC/IQ provenance and epoch handling.

The [configuration/link audit](../research/data/esp_phy_config_audit_2026-10-24.json)
records the actual generated macros, exact pinned SDK source hashes and
RF-test archive/map observations. A null macro value means undefined in
this configuration, not a guessed hardware capability.

## Remaining auxiliary-helper triage

Additional disassembly of previously unclassified names gives:

| Helper | Observed body / next boundary |
|---|---|
| `phy_current_level_set` | Stores one byte to phy_param[41]; no immediate hardware write. Meaningful consumers must be traced before treating it as a live bias knob. |
| `phy_rx_loop_cap_set` | Reads frequency-dependent channel caps and calls phy_txcap_setting. This is not the isolated LO-buffer cap field already measured; complete coupled semantics remain to be established. |
| `phy_pwdet_always_en`, `phy_pwdet_onetime_en` | Both are single ret stubs in the exact archive; no always-on noise source can be switched by these APIs. |
| `phy_en_pwdet` | Clears bits3:1 at0x600a0808. A reversible saved-mask probe is possible; the name does not establish polarity or a receive-noise benefit. Not measured yet. |
| `phy_hemu_ru26_good_res` | Sets bit24 and clears bit25 at0x600a7890. Another bounded packet-policy probe is possible; not measured yet. |
| `phy_rxevm_en` | Owns fields at0x600a7a38/7a34 and calls a reset routine that pulses7a34[1] and7c50[8]. Diagnostic/reset state is not restored by simply writing the original enable; not called as a noise toggle. |
| `phy_ulp_ext_code_set` | Couples analog0x61 register state and phy_param[652:653], or accepts an arbitrary caller code in its other branch. Factory-code source, consumers and complete restore need further audit; no guessed code is written. |

The 5805-MHz LLTF clue received an independent order-controlled
confirmation: candidate00 measured -0.006 dB at5805 and -0.002 dB
at5865, comparable to shams/unchanged10 controls. No additional benefit
is established. This ledger distinguishes static findings, feasible future
probes and actual board evidence.

The auxiliary suite (`{`, suite13) now tests the exact recovered HEMU mask
0x03000000/value0x01000000 at0x600a7890 and PWDET mask0x0e/value0
at0x600a0808, bracketed by two explicit sham executions. Each execution
uses sixteen snapshots per ABBA stage, matching held calibrated DC and
fixed gain, with the original masked state restored and verified. The
PWDET helper name does not establish enable polarity. Neither probe starts
a transmitter or calls a calibration routine. Both channels completed all256
captures. Both tested fields already match the vendor targets, so these
are no-op controls; even the5805-MHz HEMU delta of-0.050 dB is variation.
ULP analog0x61/host0/registers4..6 and phy_param[652:653] are read-only
telemetry; no ULP code is forced. Host tests cover exact auxiliary-field
rollback and unchanged ULP cache; the pinned C5 build passes.

The readbacks show reference0x58, active0x57, reg5=0x47 (bit6 set),
and caches652/653 both zero. The exact `phy_ulp_ext_code_set(0,code)`
branch writes only reg6. Its initialization branch copies reg4 to reg6,
sets bit6 and updates both cache bytes. The tracking helper computes a
temperature-derived adjustment, bounded to +/-15, updates cache653 and
calls the code-only branch. The meaning/voltage scale of one code remains
undocumented here. No supply or sensitivity benefit is assumed.

Suite14 (`}`) compares the existing reg6 code with the chip's own reg4
reference, only if bit6 is already set and the code difference is at most
one. Explicit same-code comparisons bracket the reference trial. Reg4,
reg5, reg6 and both caches are checked before/after every snapshot;
only reg6 is written and restored. Initialization/tracking are not called,
and cache/enable values remain untouched. Host tests cover refused inputs,
apply failure, unexpected code movement and fatal restore failure. Hardware
results are pending.
