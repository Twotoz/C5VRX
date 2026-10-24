# Extended PHY audit: board measurements, 2026-10-24

C5VRX by Twotoz and contributors. [Source](https://github.com/Twotoz/C5VRX); [official website and Discord invite](https://twotoz.github.io/C5VRX/).

This extends [the first NF/11p bench](NF_PHY_BENCH_2026-10-24.md) and [the static feature inventory](PHY_FEATURE_AUDIT_2026-10-24.md). All tests used the same USB-connected C5 with the VTX off. No new default or RF sensitivity/range improvement is established by these noise-only probes.

## Method and quality

Each entry uses STOCK → TEST → TEST → STOCK, four separately contiguous 8,190-byte IQ40 captures per stage (16 per stage for the large joint probes), same gain/lane/frequency/offset, exact saved-mask rollback. H/V test individual RX/TX/correction/filter/packet profiles; E repeats output ON→OFF→OFF→ON three times at one fixed gain.

The first audit at 5865 MHz selected G82 under the earlier four-window 0.2% rail screen. Later DC variation/interference caused substantial signed-rail flags. Its complete derived data remain in the archive; the apparent −0.600 dB PKDADC result had −1.057 dB stock drift and does not establish a gain. One FFT profile lost a USB capture and is marked incomplete despite successful hardware rollback.

The strengthened screen requires zero observed signed rails and `abs(mean)+6*sigma <= 6 cells` on each axis in all four qualification captures, with m2 > 0.67 cells². It reserves headroom to the ultrafine slice rails; it cannot guarantee freedom from later interference or folding. The lower selected gain increases the relative quantization contribution: compare within-run deltas, not absolute powers across different gains. The mean still retains all integrity-valid outliers.

## Results with strengthened headroom

| Profile | 5865 MHz delta / quality | 5805 MHz delta / quality |
|---|---:|---:|
| RX_0438_BIT29_OFF | +0.765 dB / NO_FLAGS | +1.697 dB / FLAGGED |
| RX_0438_BIT30_OFF | +0.646 dB / NO_FLAGS | +0.612 dB / NO_FLAGS |
| RX_0438_BIT31_OFF | +0.434 dB / NO_FLAGS | +0.331 dB / NO_FLAGS |
| RX_0438_ALL_OFF | +0.742 dB / NO_FLAGS | +0.796 dB / NO_FLAGS |
| TX_0C0C_BIT13_OFF | +0.056 dB / NO_FLAGS | -0.005 dB / NO_FLAGS |
| TX_0C0C_BIT14_OFF | +0.088 dB / NO_FLAGS | -0.008 dB / NO_FLAGS |
| TX_0C0C_ALL_OFF | -0.012 dB / NO_FLAGS | +0.101 dB / FLAGGED |
| RX_COEFF_A_ZERO | -0.041 dB / NO_FLAGS | -0.003 dB / NO_FLAGS |
| RX_COEFF_B_ZERO | +0.025 dB / NO_FLAGS | -0.018 dB / NO_FLAGS |
| RX_COEFF_AB_ZERO | +0.007 dB / NO_FLAGS | -0.043 dB / NO_FLAGS |
| PKDADC_BIT31_OFF | +0.005 dB / NO_FLAGS | -0.065 dB / NO_FLAGS |
| BB_CCA_BIT30_OFF | -0.001 dB / NO_FLAGS | +0.111 dB / FLAGGED |
| MAC_CCA_DISABLE | +0.011 dB / FLAGGED | -0.012 dB / NO_FLAGS |
| MAC_CCA_ENABLE | +0.052 dB / NO_FLAGS | +0.012 dB / NO_FLAGS |
| DFILT_MODE4 | +0.241 dB / NO_FLAGS | +0.310 dB / NO_FLAGS |
| DFILT_MODE8 | +0.402 dB / NO_FLAGS | +0.038 dB / NO_FLAGS |
| DFILT_MODE12 | +0.733 dB / NO_FLAGS | +0.403 dB / NO_FLAGS |
| FFT_FORCE_OFF | -0.130 dB / NO_FLAGS | +0.056 dB / NO_FLAGS |
| FFT_FORCE_SCALE0 | +0.056 dB / NO_FLAGS | -0.012 dB / NO_FLAGS |

Positive means higher captured IQ noise than stock. The RX enable register has a clear variance effect; the TX enables and zeroed RX coefficient fields are much smaller. That does not tell whether wanted analog FM phase, image rejection or colour survives either state. No RX calibration is promoted or disabled in production.

Packet/CCA fields have no reproducible noise benefit here. Digital modes 4/8/12 are comparisons against existing production mode0; lower noise in mode0 is already included in stock and is not an additional gain earned by this PR. Converter selector is preserved.

FFT_FORCE_OFF is a **no-op control** in the measured baseline: `600A7800=0f07a2d8` has bit19 already clear and is unchanged during that profile. Its fluctuating delta is estimator/environment variation, not an extra filter benefit. MAC_CCA_ENABLE also matches the baseline zero masked value. Do not add no-op or sign-changing deltas to 11p.

## Video-output self-noise

| Run | Frequency / gain | OFF minus ON IQ noise | Quality |
|---|---|---:|---|
| audit_5805_dac | 5805 / G73 | +0.210 dB | FLAGGED |
| audit_5865_dac | 5865 / G75 | -0.064 dB | NO_FLAGS |

These are three separately restored ON/OFF comparisons per run. The output resumes with the same program/LUT/output level through the existing verified restart. No physical loaded-CVBS or picture-quality measurement was possible. A quiet powered-off output is not an operating receiver configuration or a gain to stack onto 11p.

## Coupled ADC selector

| Run | Frequency / gain | Selector 0 vs incoming stock | Selector 1 vs incoming stock |
|---|---|---:|---:|
| audit_5805_adc | 5805 / G74 | -0.070 dB / FLAGGED | +0.038 dB / NO_FLAGS |
| audit_5865_adc | 5865 / G73 | -0.142 dB / NO_FLAGS | +0.055 dB / NO_FLAGS |
| audit_5865_adc_repeat1 | 5865 / G75 | -0.079 dB / NO_FLAGS | -0.060 dB / NO_FLAGS |
| audit_5865_adc_repeat2 | 5865 / G76 | -0.040 dB / NO_FLAGS | -0.082 dB / NO_FLAGS |

The pinned `phy_adc_rate_set` writes analog block66/host0/reg4[2] = !selector plus digital `600A0448[1:0]` = duplicated selector. S applies only the documented-by-disassembly selector values 0/1 and restores both original owned fields, preserving unrelated bits. Digital filter mode, gain, calibration flags and tuning stay fixed. Every capture checks both digital fields and the analog bit.

At 5865 MHz the incoming vendor selector is 1; at 5805 MHz it is 0. Thus selector0 at 5805 and selector1 at 5865 are no-op controls. At 5865 the selector0 deltas −0.142/−0.079/−0.040 dB do not convincingly separate from no-op selector1 deltas +0.055/−0.060/−0.082 dB. The initial apparent reduction did not hold up as a robust independent benefit. Keep the vendor selection; no fixed selector0 production change is justified. Capture sample rate metadata refers to PARLIO RX40, not a separately measured RF ADC clock or proof of desired FM transfer.

Read-only gain-tuple records confirm RF stage8/code487 at G73/G74; the highest RF stage starts at G54 and the current table max is G83. The listening receiver is not accidentally using a lower RF stage and compensating solely with BB gain. Those labels come from the pinned generator and hardware-matched AGC table; they do not calibrate LNA noise figure or authorize unlisted RF bias codes.

## Calibration-policy comparisons

Effective production policy is partial calibration with NVS data storage. A local full-calibration build disables only the PHY calibration-and-storage option and selects full RF calibration; IDF `phy_init.c` then uses its full-calibration branch without loading/storing a replacement PHY NVS record. No NVS erase is used. This is a boot experiment, never an in-place MMIO toggle. Its results must be listed separately from within-boot ABBA deltas and the partial-calibration build must be restored afterwards. The complete [fixed-gain before/full/after measurements](PHY_CALIBRATION_BENCH_2026-10-24.md) are now archived separately; partial policy was restored.

## Joint probes excluding additional 11p changes

Two predeclared combinations apply ADC selector0, packet ADC/CCA controls and channel-B arg1, plus either RX-sense0 or RX11b0. Each uses 16 captures per stage, forward/reverse profile order and an unchanged stock sham. Historical NO11P names mean that the probe makes no 11p writes; they must not be read as evidence that an incoming baseline has 11p disabled. No small single-control deltas are added together.

| Run | Profile | Noise delta dB | Stock drift dB | Quality/status |
|---|---|---:|---:|---|
| audit_5805_combo_big1 | COMBO_SENSE0_NO11P | | | INCOMPLETE |
| audit_5805_combo_big1 | COMBO_RX11B0_NO11P | | | INCOMPLETE |
| audit_5805_combo_big1 | STOCK_SHAM_NO11P | -0.196 | -0.113 | FLAGGED |
| audit_5865_combo_big1 | COMBO_SENSE0_NO11P | -0.015 | -0.503 | FLAGGED |
| audit_5865_combo_big1 | COMBO_RX11B0_NO11P | -0.007 | +0.002 | FLAGGED |
| audit_5865_combo_big1 | STOCK_SHAM_NO11P | -0.067 | -0.145 | FLAGGED |

5865 MHz shows no established combined improvement: approximately -0.015/-0.007 dB versus a -0.067 dB sham. At 5805 MHz two damaged USB captures invalidate both candidate comparisons; the sham alone is not an intervention benefit. All integrity-valid samples and quality flags remain included. Exact hardware rollback succeeded.

## Fixed physical-gain sweep

| Frequency MHz | Gain | Mean complex variance cells² | Maximum signed-rail occupancy |
|---|---:|---:|---:|
| 5805 | 72 | 0.6831 | 0.000% |
| 5805 | 74 | 0.9736 | 0.000% |
| 5805 | 76 | 1.4451 | 0.000% |
| 5805 | 78 | 2.1700 | 0.012% |
| 5805 | 80 | 3.3616 | 0.159% |
| 5805 | 83 | 6.3527 | 4.676% |
| 5865 | 72 | 0.6983 | 0.000% |
| 5865 | 74 | 1.0112 | 0.000% |
| 5865 | 76 | 1.5027 | 0.000% |
| 5865 | 78 | 2.2214 | 0.000% |
| 5865 | 80 | 3.3807 | 0.171% |
| 5865 | 83 | 6.4122 | 6.471% |

Each gain has 32 captures across forward/reverse order. G83 has approximately 2.8 dB more output noise than G80, consistent with the extra gain also applied to a wanted signal. This is not an input-referred noise or SNR comparison. Higher signed-rail occupancy does warrant a same-G83 DC-centering experiment. Rails/jump proxies do not directly count folding errors in the sign-preserving ultrafine lane. Production max remains unchanged: a lower maximum requires desired-signal/weak-carrier evidence.

## Archived evidence

- 2720 valid IQ captures in this audit archive; all rejected transports are separately recorded.
- [All run/profile results, quality and provenance](../research/data/phy_audit_2026-10-24_summary.json).
- [Derived capture statistics and raw SHA-256 hashes](../research/data/phy_audit_2026-10-24_captures.csv.gz).
- [All 503 pinned-library code symbols with name-based triage](../research/data/esp_phy_inventory_2026-10-24.json). A name category or linked definition is not functional or runtime evidence.
- Raw IQ/logs, per-run PSD arrays/plots, firmware provenance and build logs remain local under `research/data/nf_phy_2026-10-24/`; raw-log and manifest hashes are in the summary.
- NBW normalization, kurtosis/rails/drift flags and transport checks are exactly as described in [NF_AUTO_LAB.md](NF_AUTO_LAB.md). No calibrated antenna noise temperature, input carrier power, video SINAD or usable attenuation was measured.
