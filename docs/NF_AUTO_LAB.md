# ESP32-C5 undocumented NF-auto register lab (bench only)

The C5VRX-5 firmware includes a manual **J** console experiment on branch
`lab/nf-auto-16state` (based on `feat/p64-on-dfilt0`). **This is an untested
firmware experiment. No actual reduction in input-referred noise figure,
C/N, video SINAD, or range is established.**

## Provenance and limits

The pinned ESP32-C5 `libphy.a` analyzed in C5VRX issues
[#150](https://github.com/Twotoz/C5VRX/issues/150),
[#153](https://github.com/Twotoz/C5VRX/issues/153) and
[the analog-lock lab](../legacy/c5vrx4/docs/analog-lock-phy-lab.md)
contains `phy_noise_floor_auto_set()`, which sets the four fields below.

| Test-pattern bit | Bit in PHY register |
| --- | --- |
| 0 | `0x600A7018[23]` |
| 1 | `0x600A7018[28]` |
| 2 | `0x600A7C44[0]` |
| 3 | `0x600A7C50[0]` |

Four bits produce 16 possible **raw patterns**, **not 16 documented
operating modes**. Bits may control coupled logic. `0000` and `1111`
are the two historically proposed all-off and all-on states. The function
name does not prove that these bits modify analog thermal noise, the LNA
noise figure, or raw MODEM_DIAG I/Q. They might only control Wi-Fi
noise-floor estimation or packet detection.

## Running the lab

1. **On a stationary bench without a flying drone**, turn the VTX **OFF**.
   Keep any receiver antenna connected. Boot the test firmware, allow
   startup calibration to finish, and avoid scans, menus and retuning.
2. Connect USB serial; press **J** once. Do not send channel/menu commands
   during the sweep. The gain observer pauses and the board forces the
   vendor table's maximum gain for noise-only measurements.
3. Record the complete console output. The test prints `NF_AUTO_BEGIN`,
   then **32 NF_AUTO_ROW records** (all 16 patterns in Gray-code order,
   then reverse) and `NF_AUTO_END`.
4. Confirm `rows=32`, `restored=1`,
   `status=PASS`, `actual_pattern` equals the `pattern` from BEGIN,
   and the end-of-run baseline drift is small.
5. The experiment restores only the four saved bits, preserving all
   unrelated live register fields, restores the original gain, and resumes
   the gain observer. It never stores a profile in NVS.
   **On any failed restore, reboot before using the receiver.**

The experimental PHY states **must not be tested while flying**, and
the routine refuses recent/confirmed video. A USB-only reset returns
the firmware to its normal boot configuration; this lab is not a persistent
settings menu.

## Interpreting the output

`m2` is the DC-centered second moment of the existing 4-bit I/Q sample
stream, averaged from 48 valid probe windows. `kurt` can help flag
impulsive noise or corrupted samples. `noise_delta_db` is
`10*log10(m2 / baseline_m2)` **at the same gain setting**.
A reported negative value does **not** prove a sensitivity improvement:
a hidden stage could attenuate both carrier and noise, alter the
capture transfer function or change the estimator's statistics.

- Compare **both passes** for every pattern. Treat inconsistent improvements
  or a drifting end baseline as inconclusive.
- A lower `m2` warrants a second A/B/A test with a **fixed calibrated
  weak RF signal and an attenuator**, verifying that carrier level is not
  attenuated as much as the noise.
- On the live VTX test, measure *input-referred* C/N, raw IQ quality,
  U80P64-R1 video SINAD, line lock, chroma/details and usable attenuation
  threshold at a 75-ohm-loaded CVBS output.
- Repeat at relevant FPV frequencies (e.g. 5805 and 5865 MHz), on
  different boards and after retunes. Watch for clipping, gain/frequency
  offsets, spur changes, and calibration side effects.
- A new production default would require reproducible improved attenuation
  tolerance and a complete safe vendor-state restoration test.

**Do not confuse a quiet noise-only capture with a higher receive SNR.**

## Extended PR #254 bench suite (console B)

This extends C5VRX by Twotoz and contributors' pinned PHY lab in
`legacy/c5vrx4/main/phy_rx_lab.c` (#150–155). The 11p observation is credited
to SushiDude (@Ready4Sushi), as in the original lab documentation.

`B` is an explicit noise-only experiment (300 ms dwell); `C` runs the same
suite with 1200 ms dwell. Both are serviced by the existing calibration
task after startup. The receiver observer pauses; a recursive PHY transaction
excludes other gain, tuning, filter and sync PHY changes for the entire suite.
The original gain is restored afterwards. No lab state is written to NVS.
The exact `libphy.a` SHA-256 must match the existing build-time pin, otherwise
the suite refuses all private writes. Console `J` retains the original 32-row
Gray/reverse-Gray sweep, with a PHY lease and unsupported-library refusal.

The suite selects the highest vendor gain row with noise m2 > 0.67 cells²,
kurtosis 1.75..2.4, and fewer than 0.2% signed rail samples in each of four
screening captures. It then holds that
gain across all profiles. This screen is a clipping proxy, not proof that
the sign-preserving ultrafine lane never folds; inspect raw IQ and each
profile's rails/fold proxy. A lower total IQ variance is not a sensitivity win.

Each profile runs STOCK → TEST → TEST → STOCK. Every stage settles 300/1200 ms
and exports four separate, contiguous 8,190-byte Q4/I4 snapshots at 40 MS/s,
each with a transport checksum. Gaps between these snapshots are not samples.
Captured records include the frequency, offset, gain, timestamp, m2, kurtosis,
signed rails, relevant MMIO, analog I²C bytes and scale-selector state.

Profiles, always isolated against the saved incoming state:

| Profile | Test and restoration |
| --- | --- |
| NF-auto | All 16 four-bit raw patterns in Gray order, each with its own stock controls; masked restore of all four original bits |
| 11p | Verified `phy_11p_set(1,0)`; restore both saved `phy_param` flags, all eight analog bytes and the four recovered MMIO masks; never use `phy_11p_set(0,0)` as rollback |
| Channel A | Recovered argument-0 and argument-1 final fields separately; argument 0 includes clearing low three bits |
| Channel B | Recovered argument-0 and argument-1 bit13 states separately |
| IQ correction | Clear the complete five-bit enable set from pinned `phy_iq_corr_enable`: `600A0438[31:29]`, `600A0C0C[14:13]`; preserve calibrated coefficient/scaling fields and restore original enables |
| Spur | Read slots 0..3 first; test only recovered slot-0 enable bit13 when active, preserving its control code and coefficient words; skip inactive slot0 and keep other slots read-only |

`phy_rxiq_scale_set()` is inspected statically and its selector (`phy_param[650]`)
and live `600A043C` are recorded read-only. There is no guessed spur helper
call, coefficient rewrite, scale sweep, or combination of different overrides.
Unverified active nonzero spur slots require additional ABI work before testing.

Application, every capture and restore have MMIO-mask readback checks.
An entry that fails verification receives no test measurement and is marked
`REJECTED_ENTRY`; only after verified rollback can another independent profile
be attempted. A suite with these rejections ends `COMPLETE_WITH_REJECTIONS`,
never PASS. On the measured board all eight patterns requesting
`600A7C50[0]=1` failed entry; this bit did not latch, which is not proof that
it is universally read-only or that the vendor helper has no side effects.
Context changes, invalid IQ,
allocation/capture failure, changed gain or the 240-second suite deadline stop
the sequence. Restoration runs even on abort. Failed restoration prints a
fatal marker and reboots before the receiver observer can resume. The suite
must finish before a retune/menu/scan; do not queue commands during it.
The VTX-off refusal uses existing video evidence and cannot identify all RF
interference: the operator must actually switch the VTX off.

Record and analyse one run (Python with pyserial, numpy, scipy, matplotlib):

```sh
python tools/phy_noise_lab.py record COM10 research/data/nf_phy_2026-10-24/5865_run1
python tools/phy_noise_lab.py analyse research/data/nf_phy_2026-10-24/5865_run1
python tools/phy_noise_lab.py record COM10 research/data/nf_phy_2026-10-24/5865_long --key C
```

Use a fresh directory for every repetition. The tool saves the complete console
log, individually checksummed `.iq` files, SHA-256 manifest, per-capture CSV,
summary JSON, compressed Welch PSD arrays and PNG plots. PSD is computed on
each contiguous snapshot separately (1024-sample Hann windows, 50% overlap),
then averaged per stage. Equivalent rectangular noise bandwidth is integrated
complex PSD divided by mean PSD at ±0.5..2 MHz. This reference must be reported;
it is not a calibrated RF transfer-function measurement and spurs can bias it.
DC, signed rails, large-jump folding proxy, I/Q variance ratio and I/Q correlation
are available in `captures.csv`. No Gaussian-noise assumption can establish
image rejection from this noise-only comparison.

The summary compares equal-weight stock and test means, reports end-stock
drift and agreement of the two test blocks, and refuses complete-profile
statistics without all 16 captures and a successful restoration marker.
Transport-corrupted captures are retained as explicit manifest rejections and
excluded from analysis; their profile remains incomplete. The archived log is
unchanged. Quality flags mark rails above 0.2%, kurtosis outside 1.8..2.3,
stock drift above 0.15 dB or test-block disagreement above 0.15 dB. These are
diagnostic thresholds, not a calibrated confidence interval. Means retain
every valid capture, including impulsive outliers; the separate median result
is diagnostic and never silently replaces the mean. Firmware PASS concerns
lifecycle/capture, not a clean-noise or sensitivity acceptance.
Repeat at 5805/5865 MHz, after retune/reboot and with longer dwell if useful.
Do not cherry-pick the lowest of 16 noisy rows as an improvement.

Phase 2 requires a fixed calibrated weak VTX and attenuator, measuring signal
response alongside noise and equivalent usable video. This noise-only command
deliberately refuses recent video and reports carrier delta, input-referred
C/N and video SINAD as unknown. USB IQ noise measurements alone cannot supply
75-ohm-loaded physical CVBS quality, PAL/NTSC lock or attenuation tolerance.

Validation commands:

```sh
python test/test_phy_noise_lab.py
python test/test_phy_noise_lab.py --c  # C compiler available as cc
idf.py build
```

The C test compiles the actual production lab source with hardware stubs and
injects failed entry, lost context and failed rollback. Host tests are software
evidence only; actual board measurements and limitations belong in the dated
measurement report.

Board results: [2026-10-24 noise-only bench report](NF_PHY_BENCH_2026-10-24.md).
