# RF calibration policy: fixed-gain witnesses, 2026-10-24

C5VRX by Twotoz and contributors. [Source](https://github.com/Twotoz/C5VRX); [official website and Discord invite](https://twotoz.github.io/C5VRX/).

The VTX remained off. This tests whether the current partial-calibration boot policy is leaving a visible noise difference compared with a fresh full RF calibration. It does not measure noise figure or RF sensitivity.

## Controlled changes

- Partial → full → partial boot sequence; both 5805 and 5865 MHz at fixed G78 and G72.
- Full-calibration build: PHY calibration/data storage disabled, full calibration selected. IDF 6.0.2 then calls full calibration without replacing the stored PHY NVS calibration record. NVS was not erased.
- Application firmware, pinned PHY, dfilt0, filter profile, IQ lanes and output remained the same. Ordinary C5VRX startup/DC repairs and passive observers still run and can update application calibration, so this is not an identical-state RF-gain comparison.
- Existing transient gain presets and checksum-verified I snapshots were used, 16 independent contiguous 8190-byte captures per gain/boot/frequency. AUTO gain was restored by the tool after each witness.
- The original partial-calibration policy was flashed back before the final control measurements. The full-calibration policy is not a production change.

## Results

| Frequency | Fixed gain | Partial before m2 | Full m2 | Partial after m2 | Full vs bracket stock | Stock drift | Flags |
|---|---:|---:|---:|---:|---:|---:|---|
| 5805 | 78 | 2.20542 | 2.12775 | 2.18430 | -0.135 dB | -0.042 dB | none |
| 5805 | 72 | 0.69092 | 0.67820 | 0.68116 | -0.050 dB | -0.062 dB | some_captures_quant_limited, non_gaussian_or_distorted |
| 5865 | 78 | 2.22357 | 2.18954 | 2.23993 | -0.083 dB | +0.032 dB | none |
| 5865 | 72 | 0.69430 | 0.70785 | 0.69152 | +0.093 dB | -0.017 dB | some_captures_quant_limited |

At G78 the full-calibration boot has slightly lower captured variance (about −0.13 dB at 5805 and −0.08 dB at 5865) than the average of the two partial controls. G72 does not show a consistent improvement and sits close to the four-bit quantization threshold. A gain-index match does not prove identical analog RF gain after recalibration; desired carrier response and usable video are unmeasured. This is not a universal extra 0.1 dB sensitivity claim, and must not be added to 11p without a matched combined experiment.

All 192 integrity-valid captures are retained; no noise outlier is removed. Boot-to-boot environment, temperature, per-gain DC tables, converter calibration and quantization limit interpretation. The local boot logs record startup repairs as well as completion. NBW uses the same complex Welch/±0.5..2 MHz normalization as the other PR254 noise reports.

## Artifacts and reproduction

- [All boot summaries, policy provenance and quality flags](../research/data/phy_calibration_2026-10-24_summary.json).
- [Per-capture derived data and raw SHA-256 hashes](../research/data/phy_calibration_2026-10-24_captures.csv.gz).
- `tools/phy_calibration_witness.py` records an existing firmware boot; `tools/analyse_phy_calibration.py` builds this archive. Both gain rows are restored to AUTO after acquisition.
- Full local raw artifacts: `research/data/nf_phy_2026-10-24/cal_*`, plus boot/flash logs and `provenance-fullcal.json`. Raw board files are ignored by git; their hashes are committed.
- Local full build used a copy of sdkconfig with `CONFIG_ESP_PHY_CALIBRATION_AND_DATA_STORAGE` disabled, `CONFIG_ESP_PHY_RF_CAL_FULL=y`, `CONFIG_ESP_PHY_CALIBRATION_MODE=2`, and a separate `build-fullcal` output directory. Production sdkconfig.defaults was not edited.
- The original partial-calibration build was restored and acquired again; see the after-control metadata for exact firmware identity.
