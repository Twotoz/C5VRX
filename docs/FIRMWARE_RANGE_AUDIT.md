# Firmware range audit after PR 211

C5VRX by Twotoz and the C5VRX contributors. This investigation extends the
root range simulations, field-walk research and the archived PRE-Q4/gain
work; it is not an independent rediscovery of those methods.

Baseline: main `14e190d9` / release `c5vrx5-v5.0.109`, including PR 211.
The operator's first picture impression after flashing PR 211 was positive
("lijkt vrij fantastisch"). No controlled attenuation or distance gain was
measured in that check.

## Investigation boundary

Keep the continuous hardware sample path and the verified restart contract.
Separate measured board symptoms, code defects, synthetic comparisons and
changes that still need a hardware A/B. Do not add model dB gains together:
many address the same loss and the actual receiver may already avoid it.

## Leads from the baseline

- The first captured status after flashing `ee46420` had G83 and physical
  U85Q-LP, but `copy_fail=55` of 192 control ticks and `obs_us_max=1176`.
  Scheduler suspension did not establish near-zero copy failures. These
  were startup observations, not a controlled steady-state timing test.
- The same status said `lp_release_hold_ms=0`, although the policy reset
  sets a 2 s base hold. Audit the real static initialization, not only the
  reset-based host fixtures.
- `profile_observe` still requires phase coherence >=80 in addition to
  envelope C/N. This can reject a real FM carrier because of its waveform,
  and prevent the measured deviation from unlocking matched range curves.
- Every 64-byte high-priority sentinel window computes envelope moments,
  even though the sentinel only needs clipping/gain evidence. Every normal
  gain window also computes C/N at gain-observer cadence, far above the
  50 ms control cadence. Audit unnecessary work and stale measurement use.
- AFC loss, missed observations and locked-state re-acquisition have
  separate reset rules. Audit whether missing evidence can still erase a
  good centre estimate or leave the receiver off-centre at the edge.
- Filter/deviation mismatch, residual offset, gain/quantisation placement,
  sampling phase and DC already have synthetic loss evidence in the older
  research. Re-score relevant cases against the current U85Q-LP ladder,
  rather than treating fixed U85Q as today's baseline.

Results, reproduced cases, bounded fixes and negative findings are added
below as this audit progresses.
