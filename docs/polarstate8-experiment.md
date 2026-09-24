# PolarState8 TX-only experiment

PolarState8 is an opt-in demodulator on the proven 40 MS/s raw-Q4/I4 ring. It
uses one TX BitScrambler instruction per 25 ns IQ sample and a 2048x8 LUT.
Golden remains the default and the recovery mode. The experimental menu mode
requires `6BIT@40`; selecting `4BIT@80` returns to Golden.

The LUT8 address is `O16..26`: current raw8 in `O16..23`, two LUT-returned
state bits in `O24..25`, and the previous raw-I sign retained in `O27` and
routed to `O26`. The LUT returns DAC6 in bits 0..5 and the next two state bits
in bits 6..7. A prime instruction reads the first raw byte without output;
the steady-state instruction reads one byte and writes one byte each cycle.
It keeps state in the BitScrambler across the 32 KiB ring boundary. No RX
BitScrambler, M2M pass or per-sample CPU operation is used.

`tools/build_polarstate8.py` generates a **geometric seed**, not a trained
trajectory estimator. The previous phase is represented by four sectors per
raw-I half-plane. The current raw byte supplies its full eight-bit phase and
amplitude for LUT addressing; near-origin samples are held at pedestal 20.
Every constant raw phasor converges to DAC20 after one sample. The state is
recomputed from the current raw byte; the generator has not optimized recurrent
state transitions from recorded RF IQ.

Validation so far:

- Espressif's assembler accepts two instruction bundles and all 2048 LUT8
  entries (2 KiB of LUT RAM).
- `tools/test_polarstate8.py` exhausts all 2048 LUT entries, checks all 256
  constant raw inputs, and compares the full BitScrambler source model to the
  reference state machine across a 32 KiB ring wrap.
- `tools/evaluate_polarstate8.py` reports about 15 DAC-code RMS error on one
  synthetic smooth/noisy trajectory, with about 14% rail outputs. This is a
  **poor** first score and is not evidence of clean video or better range.

All eight bits of the **current** IQ byte reach the LUT. The previous IQ is
compressed to three bits. This cannot guarantee exact adjacent FM, exact
Golden equivalence, or every 50 ns winding correction. A single 25 ns step
above 180 degrees remains ambiguous without a motion prior. Full-turn
corrections also clip at the existing 6-bit DAC gain; a full-turn internal
estimate does not imply linear full-turn video amplitude.

Use Golden for normal viewing. A live PolarState8 test needs a controlled
Golden/PolarState8 comparison with fixed RF channel, gain, bandwidth and
carrier. If PolarState8 shows static, first collect raw IQ and train/score
the state table offline; do not treat the ISA fit as proof of video quality.

## ARC V3 and observed desync

The first live PR #71 build produced recognizable video, but the user saw
rapid desync and rainbow artifacts. Read-only snapshots showed `demod=2`,
`profile=8` (ARC V3), zero PARLIO/GDMA/BitScrambler faults, and repeated
raw-IQ collapse from P45/Q99% to P1/Q0%. The vendor gain table has an RF-stage
boundary at G47: one good snapshot at G47 had P45/Q99%/zero clipping; several
nearby lower-stage snapshots had near-origin IQ. These observations establish
that the C5 did not reboot during the sampled interval. They do not prove the
cause of the RF collapse because the VTX was switched off during the later
fixed-gain probe.

This revision automatically selects active ARC V3 when PolarState8 is selected
or loaded from settings, and logs the ESP reset reason at boot. ARC V3 gains a
PolarState8-only clean-IQ target: P35..45/Q>=55% with low clipping and low
origin occupancy counts as useful phase precision instead of a request to
reduce RF gain. Its LOCK hold range also extends to P45; genuine clipping or
P>45 still requests less gain. Golden's ARC V3 thresholds stay unchanged.
This is a targeted mitigation to be checked on live hardware; it does not
address PolarState8's three-bit phase-history artifacts.

After a close-to-far VTX motion, live snapshots showed Q4 collapsing to
P1/Q0 with no PARLIO/GDMA/BitScrambler faults; other snapshots showed clean
P37..45/Q99..100 or severe clipping. This is consistent with RF gain recovery
being involved, but those snapshots alone do not prove the full transition.
ARC V3's one-second no-up guard after an overload cut also blocked gain-up
while Q4 remained at the origin. The Polar-only recovery change bypasses that
guard only when both the rolling median and current raw window meet the hard
starvation criteria, after the existing settle and persistence checks. Golden
and ordinary ARC V3 keep their prior guard. This needs a live close-to-far
comparison; it is not a fix for rainbow artifacts from the seed LUT.

## Confidence-aware LUT probe (not flashed)

`python tools/probe_polar_mmse.py` compares the geometric seed with a
tail-penalized supervised LUT on held-out synthetic Q4/I4 trajectories. The
teacher is the **clean** adjacent 25 ns FM including a 3.58 MHz chroma-like
term and deliberate true phase reversals; only the observed IQ is corrupted.
The resulting DAC table learns a loss-minimizing conditional estimate. This is
not a calibrated Bayesian posterior or an MMSE estimate under the tail loss.

There is also a deterministic failure without noise or gain changes. For a
constant +0.14 rad (about +8 degrees) step per 25 ns at amplitude six Q4
codes, the adjacent teacher is DAC28 at every sample. The geometric seed emits
DAC20 for 82.2% of 2,000 samples, with occasional spikes near DAC50; its mean
is only 25.4. The forced `state == state3(raw) -> DAC20` rule and coarse phase
history turn sustained FM into sparse pulses. This can corrupt CVBS sync even
when ARC and the transport are stable; gain-switch speckles are a separate
transient shared with Golden.

For amp=6 and input noise=0.35 Q4 codes, the trained table reduced synthetic
RMS DAC error from 11.96 to 8.99 and >=16-code errors from 20.0% to 7.9%.
But with **zero input noise**, the same table passed only about 0.29 of the
teacher's chroma/trajectory variation (slope), versus 0.57 for the already
poor geometric seed. It traded valid high-frequency video for a quieter
estimate. A two-bit low-IQ hold barely changed the score and cannot freeze
the full state: the third history bit is wired to current raw-I sign. This
synthetic result rejects the naive trained LUT as a flash candidate. Real
RF IQ plus a clean synchronized reference are still needed to separate phase
glitches from legitimate video motion and evaluate chroma transfer.
