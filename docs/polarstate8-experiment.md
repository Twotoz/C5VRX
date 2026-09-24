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
