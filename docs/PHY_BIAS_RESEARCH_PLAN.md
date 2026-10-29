# PHY bias/current sensitivity experiment

C5VRX by Twotoz and the C5VRX contributors. Requested by the operator on
2026-10-28. Original plan, retained for provenance. Implementation and completed
board results are now in [PHY_NOISE_RESULTS_2026-10-28.md](PHY_NOISE_RESULTS_2026-10-28.md).

## Question and evidence

Can a supported receive configuration improve antenna-input sensitivity
through firmware-controlled analog bias/current settings?

The approximately 6 dB noise figure is an estimate from existing receive-chain
measurements, not a calibrated noise-figure measurement. Neither
`phy_current_level_set` nor `phy_bias_reg_set` is established as an LNA
noise-figure control. Function names do not establish their physical role.
Do not promise a particular dB gain before measurement.

Existing evidence: `docs/PR254_PHY_FINDINGS.md` records that
`phy_bias_reg_set` initializes analog block 0x6a, host 1, registers 0/1 to
0x9f/0x7f, whereas operating witnesses were 0xcf/0x7f. Frequency setup and
calibration also own the high nibble of register 0. Replaying initialization
is therefore not a justified sensitivity experiment. See also the excluded
controls in `docs/PHY_SENSITIVITY_LAB.md`.

## Work to implement

1. Pin the exact libphy archive and toolchain identity. Recover both functions'
   argument conventions, callers, input ranges, conditions, register masks,
   cached state and calibration dependencies from disassembly. Record concrete
   call sites and register diffs. Determine whether either function actually
   affects the active 5 GHz RX path before designing a sweep.
2. Define candidates only for fields whose semantics and vendor-used values
   are understood. Record stock readback after channel setup. Keep unrelated
   bits intact; exclude arbitrary byte or nibble sweeps.
3. Prototype in the bench sensitivity lab. If exact restoration is established,
   use its existing saved-state/readback coordinator and interleaved shams.
   Otherwise use separate boot candidates with a verified stock boot between
   trials. A reboot is not assumed to reset every modem-domain register:
   verify the restored state and use complete power removal when necessary.
4. Verify that candidate settings persist through gain/DC writes and channel
   setup. Record DC, gain-table identity, filter state, sampling phase and
   transport faults alongside every candidate. Refuse unsupported PHYs and
   stop a run if readback or restoration fails.

## Measurement protocol

- Bench only, with a stationary weak VTX and unchanged channel, power,
  antennas, positions and receiver configuration. Keep people away during
  captures. Prefer a conducted source with known attenuation for final proof.
- Repeat stock/candidate/stock trials, randomize candidate order, and repeat
  at several input levels near the reception threshold and on more than one
  channel. First measure stock-versus-stock scatter.
- Compare raw IQ carrier and noise power, DC, clipping/folding, I/Q balance,
  phase behavior, and decoded sync/SINAD. Use M2M4 as a diagnostic with its
  quantization, folding and constant-envelope assumptions checked. A lower
  measured noise amplitude alone can simply mean lower gain or bandwidth.
- Keep bandwidth and signal transfer comparable. Any candidate that narrows
  the filter must be reported as a bandwidth tradeoff, not lower noise figure.
- Report the current `video_pct` as AFC measurement availability. A decoded
  sync counter is additional evidence, not a complete measure of visible
  picture quality or a measurement after the physical DAC and goggles.
- Record firmware/archive hashes, raw captures, settings and trial order.
  Assess paired differences against stock scatter and report uncertainty.

## Acceptance and follow-up

Promote a candidate only after repeated improvement at equal input signal,
with unchanged useful bandwidth and no DC, distortion, strong-signal,
transport or restoration regression. Confirm on independent runs. Establish
absolute sensitivity with a calibrated input; do not infer absolute NF from
uncalibrated amplitude changes.

If no active RX consumer or useful candidate is established, document that
negative result and stop this path. Keep experimental controls out of normal
boot and flight operation until this evidence exists.

Separate follow-ups: log receiver pause ownership/duration to explain missing
flight-log seconds; evaluate LEVEL fold memory independently. The unfinished
fold-memory work remains separate from this experiment.
