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
