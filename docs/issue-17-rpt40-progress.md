# Issue 17: RPT40 checkpoint

Branch: `codex/issue-17-rpt40`, based on current main `c34139f`.

Baseline is Golden Phase5, not PR18 True40. The explicit
`sdkconfig.golden-phase5.defaults` overlay preserves positive RX edge and
disables alternative demodulators. Full Q4/I4 mapping, Golden LUT gain and
pedestal, and two-sample (50 ns at 40 MS/s) discrimination are retained.

## Implemented, not yet hardware-qualified

- Full 256-entry raw IQ to Phase5 plus magnitude-confidence RX kernel.
- Interleaved even/odd 50 ns TX kernel producing one byte per input byte.
  This is cadence-only: hardware confidence suppression is NOT implemented.
- Host reference for confidence-based hold/recovery, always advancing phase
  history. Its thresholds are proposals, not validated quality improvements.
- RF-off RX feasibility firmware: bypass/mapped trials at 20 and 40 MS/s,
  deterministic 8192-byte patterns, diagnostic payloads and FIFO evidence.
- Four passing host tests covering exact mapping, actual assembly simulation,
  Golden parity equivalence, continuity and candidate recovery behavior.
- ESP-IDF v6.0.1 oracle build passes.

## Remaining hard gates / next actions

1. Add host decoder for RPT0 diagnostic records (128-byte header, raw and
   captured 8192-byte payloads; four records at diagcap + index * 0x5000).
   Validate hashes and every byte after cyclic alignment. Completion is not
   evidence of correct data or physical cadence.
2. Flash and run the RF-off oracle; establish RX mapping at 40 MS/s on C5.
3. Implement and verify hardware confidence suppression without reducing IQ
   resolution, replacing 50 ns discrimination, or resetting state at DMA wraps.
4. Integrate live streaming and measure actual DAC output/cadence/continuity.
5. Independent capture validation and hardware Golden/RPT40 A/B: >=16-code
   errors toward zero, >=32-code errors zero, static and picture no worse.
   Define the error reference explicitly; parity identity is not quality proof.

No new firmware has been flashed at this checkpoint. Keep VTX off for the
oracle; request a short, explicit VTX-on window only for live RF A/B.
Do not mark Issue 17 complete or claim a quality pass from these host tests.

Build: use `sdkconfig.defaults;sdkconfig.flash40.defaults;sdkconfig.rpt40-oracle.defaults`
with build-local SDKCONFIG in `build-rpt40-oracle`.

Tests: `python -m unittest discover -s tools -p test_rpt40.py`.

## First physical RX oracle run (2026-09-13)

Oracle flashed with verified hashes; user cold-booted and returned C5 to
download mode. Read 0x14000 bytes at 0x112000 into local ignored
`rpt40-oracle-capture.bin`. Decoder: `tools/analyze_rpt40_oracle.py`.
All four raw/output payload hashes validate.

| Requested rate | RX mapping | RX result | Cyclic byte mismatches / 8192 |
| --- | --- | --- | --- |
| 20 MS/s | bypass | success | 0 |
| 20 MS/s | Phase5 + confidence | timeout (263) | 20 |
| 40 MS/s | bypass | success | 0 |
| 40 MS/s | Phase5 + confidence | timeout (263) | 20 |

Mapped trials fail the completion/full-payload gate at both rates. The cause
is not established; do not conclude a throughput limit from this result.
No live RPT40 or static/image-quality qualification has been obtained.
User requested live CVBS firmware next: build the explicit Golden profile,
not the incomplete RPT40 candidate. Existing calibration was read and retained:
pedestal 20, gain 2, polarity 0, reference clock 20 MHz.

## Live visual feedback and baseline clarification (2026-09-13)

The fresh `build-golden-phase5` live build was subsequently flashed with
verified hashes (defaults + flash40 + golden-phase5 overlays). It is NOT a
live RPT40 implementation and contains no new confidence suppression.

User observed small teeth at the right side of the picture, occasionally a
larger tooth accompanied by light static. Initial better/worse judgment was
uncertain. User then reported that the **OG Golden Phase5** seemed better:
they did not recall that larger tooth on the OG version. They explicitly
clarified that this comparison means OG Golden Phase5, NOT RPT40.

Treat the fresh build as a possible regression relative to OG Golden Phase5,
not as an established reproduction of its hardware-proven picture quality.
The exact OG artifact/commit/configuration for this comparison has not yet
been pinned down here; recover it before claiming a controlled baseline A/B.
Do not infer equivalence solely from the new profile's Golden name.

This is subjective visual feedback, not a measured MAE or hard-error rate.
Coincident static and large teeth do not establish RF, DAC-code or timing
causation. Golden/RPT40 non-regression gates remain open. No additional
firmware changes or flash were made in response to this clarification.
