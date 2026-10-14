# AFC centering: passive evidence

C5VRX by Twotoz and the C5VRX contributors. This extends the existing AFC V2
controller, `research/afc_bias.py` and the range audit; it changes no retuning
threshold, weak-carrier gate, sticky lock or correction budget.

`T` now prints `AFC_EVIDENCE` alongside the existing raw `cfo_khz` field.

| Field | Meaning |
|---|---|
| `sticky_lock` | Acquisition lock retained while valid camera evidence continues. It is **not** proof of current RF centering. |
| `last_qualified_raw_porch_khz` | Latest admitted porch estimate relative to receiver tuning. |
| `target_khz` | Demodulator porch reference, currently -1000 kHz. |
| `last_qualified_residual_khz` | Porch estimate minus target; zero means matching that reference, not independently measured carrier truth. |
| `latest_valid`, `context_current`, `qualified_gain_current` | Latest snapshot admission and whether its saved context/gain still match. |
| `qualified_age_ms`, `sample_age_ms` | Ages at the time of `T`; -1 means unknown, changed context/gain or a future timestamp. |
| `n`, `timestamped` | Estimates in the current controller history and members with known observation times. |
| `stable`, `median_residual_khz`, `mad_khz` | Actual history median and median absolute deviation, including partial histories; `stable` still requires all16 estimates and the existing MAD bound. |
| `oldest_age_ms`, `newest_age_ms` | Ages of known history members. Together with coverage they expose sparse/stale evidence. |
| `window`, `held`, `cn_x10` | Window exclusion/hold reason and the strength evidence observed for this control turn. Unknown C/N remains -990. |

Times are **control observation-admission times**, not independently measured
RF/DMA sample timestamps. `timestamped<n` means some ages are unavailable;
do not treat the known oldest timestamp as a complete-history age bound.
The 16 timestamps add128 bytes to the control task's AFC object; the host ABI
object is232 bytes. The existing6144-byte task stack is unchanged. A full
snapshot is copied under short task exclusion, with printing outside it.
No CPU work is added to the40-MS/s sample path. Physical stack and worst-case
control timing still require board measurement.

## Actual-controller probes

`research/afc_centering_probe.c` drives the production header at its normal
50-ms cadence using artificial estimator-boundary inputs. It establishes
controller behavior, not hardware drift, an unbiased estimator or range gain:

- After initial lock,600 valid windows ramp from0 to+1000kHz residual. Lock
  stays true and corrections stay0: live retuning remains intentionally off.
- Ten missing copies retain lock. Four **observed invalid** windows drop it;
  all16 estimates survive the short fade.
- A constant+220kHz biased porch passes the MAD gate perfectly. At4dB C/N it
  causes0 corrections; marking the same input6dB strong causes a220kHz step.
  Strength/reference calibration therefore matter even with a stable median.
- Nineteen missing windows followed by one valid window each second preserve
  a full, stable history with members older than10s. The existing1s rule is a
  **consecutive miss budget**, not an absolute history-age guarantee.

`test/test_afc_evidence.c` compares1600 timed and untimed controller decisions
and verifies identical behavior, rolling timestamp alignment, MAD, sparse
history, unknown times, polarity/standard contradictions, invalidation,
changed context and own-retune budget/sign-check bookkeeping.
`test/test_afc_telemetry.py` compiles the actual `control_print` AFC section,
mutates producer data when task exclusion ends, and verifies the printed
snapshot remains coherent. It checks elapsed/unknown/future ages and changed
context/gain. Pinned IDF6.0.2 tests and the existing31 AFC checks pass.

## Smallest useful physical experiment

Use a stable known PAL/NTSC source and fixed conducted attenuation. Record
original IQ plus `T`/`R` before warm-up, during gradual frequency drift and
after brief/long fades. Keep VTX/receiver temperature and any independent
frequency reference separate; receiver temperature alone cannot identify
which oscillator moved. Confirm every physical step with before/after
porch residual, actual tuned offset, strength/freshness, history ages,
genuine-sync uptime and displayed detail. Repeat near the weak edge without
authorizing extra weak-carrier retunes.

First distinguish four cases: sticky lock with growing qualified residual;
unlocked but weak/unknown strength; unlocked with unstable/old history; and
strong fresh evidence that corrects but settles at the wrong porch reference.
These need different remedies. Do not infer correct RF centering from
`afc_locked=1`, one raw porch value, or a low MAD alone.
