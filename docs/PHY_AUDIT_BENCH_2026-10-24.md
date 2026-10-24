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
| audit_5805_bias_adc1 | 5805 / G74 | -0.040 dB / NO_FLAGS | -0.012 dB / NO_FLAGS |
| audit_5865_adc | 5865 / G73 | -0.142 dB / NO_FLAGS | +0.055 dB / NO_FLAGS |
| audit_5865_adc_repeat1 | 5865 / G75 | -0.079 dB / NO_FLAGS | -0.060 dB / NO_FLAGS |
| audit_5865_adc_repeat2 | 5865 / G76 | -0.040 dB / NO_FLAGS | -0.082 dB / NO_FLAGS |
| audit_5865_bias_adc1 | 5865 / G74 | -0.014 dB / NO_FLAGS | +0.056 dB / NO_FLAGS |

The pinned `phy_adc_rate_set` writes analog block66/host0/reg4[2] = !selector plus digital `600A0448[1:0]` = duplicated selector. S applies only the documented-by-disassembly selector values 0/1 and restores both original owned fields, preserving unrelated bits. Digital filter mode, gain, calibration flags and tuning stay fixed. Every capture checks both digital fields and the analog bit.

At 5865 MHz the incoming vendor selector is 1; at 5805 MHz it is 0. Thus selector0 at 5805 and selector1 at 5865 are no-op controls. At 5865 the selector0 deltas −0.142/−0.079/−0.040 dB do not convincingly separate from no-op selector1 deltas +0.055/−0.060/−0.082 dB. The initial apparent reduction did not hold up as a robust independent benefit. Keep the vendor selection; no fixed selector0 production change is justified. Capture sample rate metadata refers to PARLIO RX40, not a separately measured RF ADC clock or proof of desired FM transfer.

Read-only gain-tuple records confirm RF stage8/code487 at G73/G74; the highest RF stage starts at G54 and the current table max is G83. The listening receiver is not accidentally using a lower RF stage and compensating solely with BB gain. Those labels come from the pinned generator and hardware-matched AGC table; they do not calibrate LNA noise figure or authorize unlisted RF bias codes.

## Calibration-policy comparisons

Effective production policy is partial calibration with NVS data storage. A local full-calibration build disables only the PHY calibration-and-storage option and selects full RF calibration; IDF `phy_init.c` then uses its full-calibration branch without loading/storing a replacement PHY NVS record. No NVS erase is used. This is a boot experiment, never an in-place MMIO toggle. Its results must be listed separately from within-boot ABBA deltas and the partial-calibration build must be restored afterwards. The complete [fixed-gain before/full/after measurements](PHY_CALIBRATION_BENCH_2026-10-24.md) are now archived separately; partial policy was restored.

## Joint probes excluding additional 11p changes

Two predeclared combinations apply ADC selector0, packet ADC/CCA controls and channel-B arg1, plus either RX-sense0 or RX11b0. Each uses 16 captures per stage, forward/reverse profile order and an unchanged stock sham. Historical NO11P names mean that the probe makes no 11p writes; they must not be read as evidence that an incoming baseline has 11p disabled. No small single-control deltas are added together.

| Run | Profile | Noise delta dB | Stock drift dB | Quality/status |
|---|---|---:|---:|---|
| audit_5805_combo_ack1 | COMBO_SENSE0_EXTRA | -0.010 | -0.025 | NO_FLAGS |
| audit_5805_combo_ack1 | COMBO_RX11B0_EXTRA | +0.001 | +0.012 | NO_FLAGS |
| audit_5805_combo_ack1 | STOCK_SHAM_EXTRA | +0.001 | -0.024 | NO_FLAGS |
| audit_5805_combo_big1 | COMBO_SENSE0_NO11P | | | INCOMPLETE |
| audit_5805_combo_big1 | COMBO_RX11B0_NO11P | | | INCOMPLETE |
| audit_5805_combo_big1 | STOCK_SHAM_NO11P | -0.196 | -0.113 | FLAGGED |
| audit_5865_combo_ack1 | COMBO_SENSE0_EXTRA | +0.001 | +0.047 | NO_FLAGS |
| audit_5865_combo_ack1 | COMBO_RX11B0_EXTRA | -0.037 | +0.011 | NO_FLAGS |
| audit_5865_combo_ack1 | STOCK_SHAM_EXTRA | +0.023 | -0.024 | NO_FLAGS |
| audit_5865_combo_big1 | COMBO_SENSE0_NO11P | -0.015 | -0.503 | FLAGGED |
| audit_5865_combo_big1 | COMBO_RX11B0_NO11P | -0.007 | +0.002 | FLAGGED |
| audit_5865_combo_big1 | STOCK_SHAM_NO11P | -0.067 | -0.145 | FLAGGED |

The initial pre-merge 5865 MHz run showed no established combined improvement: approximately -0.015/-0.007 dB versus a -0.067 dB sham. At 5805 MHz two damaged USB captures invalidate both candidate comparisons; the sham alone is not an intervention benefit. All integrity-valid samples and quality flags remain included. Exact hardware rollback succeeded.

Complete ACK-enabled repeats on the integrated 11p baseline give sense/RX11b combinations -0.010/+0.001 dB at 5805 MHz (sham +0.001) and +0.001/-0.037 dB at 5865 MHz (sham +0.023), with no quality flags. The small RX11b-combination reduction changes sign across frequencies; no reproducible 0.4 dB joint benefit or input-referred improvement is established.

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

Each gain has 32 captures across forward/reverse order. G83 has approximately 2.8 dB more output noise than G80, consistent with the extra gain also applied to a wanted signal. This is not an input-referred noise or SNR comparison. Higher signed-rail occupancy does warrant a same-G83 DC-centering experiment. This historical sweep changed gain while the receiver was paused: its normal post-gain DC hook intentionally returns early, leaving the held DC calibration released. Thus these rail figures describe vendor DC at forced gain, not the corrected live receiver. Other historical within-run probes also used this paused forced-gain baseline. The new DC bench explicitly applies a matching loaded calibration row and restores the original outer gain/DC hold before resume. Rails/jump proxies do not directly count folding errors in the sign-preserving ultrafine lane. Production max remains unchanged: a lower maximum requires desired-signal/weak-carrier evidence.

## Same-gain calibrated DC comparison

The new bench applies a matching, already-loaded production DC row before each fixed-gain comparison. Its bounded local search uses separate probe samples, restores entry codes, then acquires 16 fresh IQ windows per ABBA stage. No gain/filter/IQ-correction changes occur inside a comparison. Entry codes and the held mode are restored.

| Run | Gain | Noise delta dB | Mean signed rails STOCK / TEST | Quality |
|---|---:|---:|---:|---|
| audit_5805_dco_ack1 | 78 | +0.006 | 0.0000% / 0.0000% | NO_FLAGS |
| audit_5805_dco_ack1 | 80 | -0.003 | 0.0000% / 0.0000% | NO_FLAGS |
| audit_5805_dco_ack1 | 83 | -0.005 | 0.3072% / 0.0527% | FLAGGED |
| audit_5865_dco2 | 78 | +0.003 | 0.0000% / 0.0000% | NO_FLAGS |
| audit_5865_dco2 | 80 | +0.011 | 0.0000% / 0.0004% | NO_FLAGS |
| audit_5865_dco2 | 83 | +0.009 | 0.1107% / 0.0424% | FLAGGED |
| audit_5865_dco3 | 78 | -0.020 | 0.0000% / 0.0000% | NO_FLAGS |
| audit_5865_dco3 | 80 | -0.007 | 0.0015% / 0.0000% | NO_FLAGS |
| audit_5865_dco3 | DCO_CENTER_G83 | | | INCOMPLETE |
| audit_5865_dco_ack1 | 78 | +0.028 | 0.0000% / 0.0000% | NO_FLAGS |
| audit_5865_dco_ack1 | 80 | +0.020 | 0.0000% / 0.0000% | NO_FLAGS |
| audit_5865_dco_ack1 | 83 | -0.005 | 0.0240% / 0.0233% | NO_FLAGS |

Lower signed-rail occupancy is a headroom observation, not a direct count of folding errors, a noise-figure improvement or a range measurement. Equal target/entry codes make a sham comparison. Stock/test means retain every valid capture, including outliers. A lower raw mean is not itself more sensitivity. Hardware-code readback and full raw-capture hashes are retained in the archive.

At 5805 MHz G83 mean signed rails fell from 0.3072% to 0.0527%, with -0.005 dB variance change. At 5865 MHz the first complete comparison gave 0.1107% to 0.0424% (+0.009 dB), but the later complete repeat gave 0.0240% to 0.0233% (-0.005 dB). Thus there is evidence for correcting an off-centre starting pair, not a repeatable extra noise reduction to add to 11p. The incomplete G83 repeat is retained. The first ACK-enabled run recovered one corrupted transport attempt and retained all 192 intended snapshots with matching capture identity; the 5805 MHz run needed no retry. The committed Windows helper was also exercised with a full-system reset and the console answered afterwards.

## Isolated LLTF/RIFS/VHT/11b-LR policy fields

These profiles use a matching calibrated DC hold at the screened fixed gain, 1200 ms dwell and the same verified transport and rollback. The initial suite has four windows per stage; the LLTF follow-up has 16. A profile matching the incoming masked register values is explicitly a no-op control.

| Run | Profile | Noise delta dB | Role | Quality/status |
|---|---|---:|---|---|
| audit_5805_lltf_big1 | LLTF_MASK_00 | -0.026 | changed bits | NO_FLAGS |
| audit_5805_lltf_big1 | LLTF_MASK_10 | +0.026 | no-op control | NO_FLAGS |
| audit_5805_lltf_big1 | LLTF_MASK_01 | +0.021 | changed bits | NO_FLAGS |
| audit_5805_lltf_big1 | LLTF_MASK_11 | -0.021 | changed bits | NO_FLAGS |
| audit_5805_lltf_big1 | POLICY_SHAM | +0.003 | no-op control | NO_FLAGS |
| audit_5805_policy1 | LLTF_MASK_00 | -0.064 | changed bits | NO_FLAGS |
| audit_5805_policy1 | LLTF_MASK_10 | +0.042 | no-op control | NO_FLAGS |
| audit_5805_policy1 | LLTF_MASK_01 | +0.043 | changed bits | NO_FLAGS |
| audit_5805_policy1 | LLTF_MASK_11 | +0.109 | changed bits | NO_FLAGS |
| audit_5805_policy1 | RIFS_ARG0 | -0.063 | changed bits | FLAGGED |
| audit_5805_policy1 | RIFS_ARG1 | -0.029 | no-op control | NO_FLAGS |
| audit_5805_policy1 | VHT_ARG0 | -0.005 | changed bits | NO_FLAGS |
| audit_5805_policy1 | VHT_ARG1 | +0.102 | no-op control | NO_FLAGS |
| audit_5805_policy1 | RX11BLR_ARG0 | -0.074 | no-op control | NO_FLAGS |
| audit_5805_policy1 | RX11BLR_ARG1 | +0.097 | changed bits | NO_FLAGS |
| audit_5805_policy1 | POLICY_SHAM | +0.000 | no-op control | NO_FLAGS |
| audit_5865_lltf_big1 | LLTF_MASK_00 | +0.036 | changed bits | NO_FLAGS |
| audit_5865_lltf_big1 | LLTF_MASK_10 | -0.012 | no-op control | NO_FLAGS |
| audit_5865_lltf_big1 | LLTF_MASK_01 | +0.013 | changed bits | NO_FLAGS |
| audit_5865_lltf_big1 | LLTF_MASK_11 | -0.054 | changed bits | NO_FLAGS |
| audit_5865_lltf_big1 | POLICY_SHAM | +0.043 | no-op control | NO_FLAGS |
| audit_5865_policy1 | LLTF_MASK_00 | -0.039 | changed bits | NO_FLAGS |
| audit_5865_policy1 | LLTF_MASK_10 | +0.031 | no-op control | NO_FLAGS |
| audit_5865_policy1 | LLTF_MASK_01 | -0.032 | changed bits | NO_FLAGS |
| audit_5865_policy1 | LLTF_MASK_11 | -0.021 | changed bits | NO_FLAGS |
| audit_5865_policy1 | RIFS_ARG0 | -0.002 | changed bits | NO_FLAGS |
| audit_5865_policy1 | RIFS_ARG1 | -0.006 | no-op control | NO_FLAGS |
| audit_5865_policy1 | VHT_ARG0 | -0.020 | changed bits | NO_FLAGS |
| audit_5865_policy1 | VHT_ARG1 | -0.080 | no-op control | NO_FLAGS |
| audit_5865_policy1 | RX11BLR_ARG0 | -0.023 | no-op control | NO_FLAGS |
| audit_5865_policy1 | RX11BLR_ARG1 | -0.015 | changed bits | NO_FLAGS |
| audit_5865_policy1 | POLICY_SHAM | -0.031 | no-op control | NO_FLAGS |

The first policy request saw 5865 MHz in early boot status, but saved-channel restore completed before the queued suite started at 5805 MHz. The immutable header and captures identify 5805; the folder was corrected to audit_5805_policy1 and a context note retained. The 5865 run was then deliberately retuned and repeated. Host acquisition now checks actual suite/capture frequency after the request as well, drains through outer restoration, and rejects a mismatch instead of silently accepting the early status.

## Adjacent LO-capacitor codes

Only block0x63/host1/reg21[4:0] changes; upper bits, bias, crystal/VCO codes, ADC selection, 11p, fixed gain and held DC pair are retained. The incoming code is a sham and adjacent codes stay inside the vendor helper interval10..31. Sixteen snapshots per ABBA stage are independently verified and the original owned field is restored before resume.

| Run | Saved / target code | Noise delta dB | Stock drift dB | Quality/status |
|---|---:|---:|---:|---|
| audit_5805_locap1 | 12 / 12 | -0.014 | +0.015 | NO_FLAGS |
| audit_5805_locap1 | 12 / 11 | +0.014 | -0.032 | NO_FLAGS |
| audit_5805_locap1 | 12 / 13 | +0.007 | +0.040 | NO_FLAGS |
| audit_5865_locap1 | 10 / 10 | +0.017 | -0.001 | NO_FLAGS |
| audit_5865_locap1 | 10 / 11 | -0.013 | -0.013 | NO_FLAGS |

The measured stock codes match the formula:10 at5865 MHz and12 at5805 MHz. Code9 at5865 is skipped as outside the vendor interval. Candidate deltas -0.013/+0.014/+0.007 dB are comparable to shams +0.017/-0.014 dB. There is no established additional noise improvement or reason to override the stock cap formula. No RF conversion-gain, PLL phase-noise or usable-video result is claimed.

## Read-only bias witness

Pinned phy_bias_reg_set initializes block0x6a/host1 reg0/reg1 to 0x9f/0x7f. The measured operating bytes are listed below. phy_freq_start_cal_5g_code subsequently writes reg0[7:4] while iterating frequency calibration, so a different operating high nibble is not evidence of a forgotten LNA optimization. No bias bytes were overridden. See the static feature audit for exact call/write evidence.

| Run | Observed reg0 / reg1 bytes |
|---|---|
| audit_5805_bias_adc1 | cf / 7f |
| audit_5805_lltf_big1 | cf / 7f |
| audit_5805_locap1 | cf / 7f |
| audit_5805_policy1 | cf / 7f |
| audit_5865_bias_adc1 | cf / 7f |
| audit_5865_lltf_big1 | cf / 7f |
| audit_5865_locap1 | cf / 7f |
| audit_5865_policy1 | cf / 7f |

## Live fixed-MAX witnesses

These separate captures leave the normal DC servo running. Console G selects transient MAX (G83); uppercase I provides independent contiguous snapshots, separated by a one-second collection interval. The tool restores AUTO. These are operating-state witnesses, not an ABBA intervention, weak-carrier test or noise-figure measurement.

| Run | Captures | Mean variance cells² | Mean / max signed rails | Mean cell-centre I/Q |
|---|---:|---:|---:|---:|
| live_5805_g83 | 32 | 6.3768 | 0.0275% / 0.0855% | +0.069/-0.064 |
| live_5865_g83 | 32 | 6.1389 | 0.0244% / 0.0611% | -0.041/+0.017 |

With its ordinary DC servo running, G83 averaged only 0.0244%/0.0275% signed-rail occupancy on 5865/5805 MHz, with worst snapshots 0.0611%/0.0855%. Mean centring was within 0.07 cell per axis. This differs substantially from the old unheld vendor-DC sweep. Together with the expected higher output noise at higher gain, these observations do not justify lowering the no-carrier maximum to G80. They also do not prove that G83 is optimal for every wanted-signal amplitude or interference case.

## Empirical unchanged-state variation

Controls are selected from explicit shams, unchanged gain-row profiles, identical entry/target DC or LO-cap settings, and exact baseline register fingerprints for documented no-op MMIO/ADC profiles. They are not selected by whether their measured delta is near zero. Every complete control remains in the JSON; the table separates the pre-existing quality flags and groups the NO_FLAGS controls by actual capture count. These mixed-context descriptive quantiles are not calibrated confidence bounds for an individual intervention or an RF sensitivity threshold.

| Captures/control | NO_FLAGS controls | Median signed delta dB | Median absolute delta dB | 95th percentile absolute delta dB |
|---|---:|---:|---:|---:|
| 16 | 24 | -0.019 | 0.038 | 0.099 |
| 32 | 9 | +0.006 | 0.008 | 0.049 |
| 64 | 8 | +0.007 | 0.013 | 0.037 |
| 128 | 2 | +0.012 | 0.012 | 0.022 |

51 complete controls were classified; 8 have quality flags and are retained separately.
A small favourable point estimate must repeat against these controls; adding selected negative fluctuations does not establish an extra combined gain. The actual joint comparisons remain the relevant noise-only evidence.

## Archived evidence

- 5631 valid audit IQ captures plus 64 live fixed-MAX witnesses; all rejected transports are separately recorded.
- [All run/profile results, quality and provenance](../research/data/phy_audit_2026-10-24_summary.json).
- [Derived capture statistics and raw SHA-256 hashes](../research/data/phy_audit_2026-10-24_captures.csv.gz).
- [All 503 pinned-library code symbols with name-based triage](../research/data/esp_phy_inventory_2026-10-24.json). A name category or linked definition is not functional or runtime evidence.
- Raw IQ/logs, per-run PSD arrays/plots, firmware provenance and build logs remain local under `research/data/nf_phy_2026-10-24/`; raw-log and manifest hashes are in the summary.
- NBW normalization, kurtosis/rails/drift flags and transport checks are exactly as described in [NF_AUTO_LAB.md](NF_AUTO_LAB.md). No calibrated antenna noise temperature, input carrier power, video SINAD or usable attenuation was measured.
