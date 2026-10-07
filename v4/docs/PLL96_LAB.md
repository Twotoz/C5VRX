# Quantized PLL96 board experiment

## Correction and physical failure (2026-10-08)

Build `84aadd1` was flashed and booted in PLL96 mode, but the operator reported
static, tearing and unusable video. It is rejected. The generator incorrectly
decoded I from the low nibble and Q from the high nibble. The project encoder
contract is I-high/Q-low; swapping them reflects complex phase and reverses FM
and CVBS polarity. The earlier per-method fitted gain could be negative, hiding
this defect in SINAD. Historical weak results below must not justify promotion.

Correcting only I/Q restores positive polarity but still misses all strong
horizontal pulses in the new full-frame diagnostic. The coarse recursive loop
and its output reconstruction remain material problems; I/Q was not the only
cause. A separate 4,096-configuration repair study and 352 reconstruction
checks produced the pinned **16 phase / 6 frequency** LAB in
`tools/pll96_model.json`. It retains eight observed phase tokens and the same
96-state, LUT16/2KiB, eight-slot, two-bundle transport. In seed9801 full PAL/NTSC
30-dB-C/N diagnostics it misses no H/V pulses and detects both vertical trains.
It still loses strong SINAD, raises sync-tip error and, for NTSC, timing jitter.
This is an operator-requested hardware comparison, not a successful demod upgrade.

After flashing runtime source `60ad240` on COM10 and confirming active
`PLL96 IQ FIX LAB`, the operator reported that video otherwise looks fine,
but has substantial perceived gain noise. This is partial positive picture
feedback, not full picture/range acceptance. A read-only snapshot during that
report showed IQ coherence98, clip_pm0, origin_pm0, gain36, and zero TX-empty,
RX-overflow and GDMA error counters. Gain changed during reception, so this
single observation neither proves a fixed gain fault nor isolates demodulator
noise. RF strength and a matched OVP56 visual comparison were not established.

Old persisted value5 falls back to OVP56. The corrected experiment uses value6;
uppercase serial `P` opts in and toggles back to OVP56 with a saved reboot.
Lowercase `p` again performs the existing snapshot diagnostic. Normal `g` only
cycles values0..4. OVP56 remains the new/invalid-selection default. Source tests
cover signed nibble decoding, all startup states, state continuity, selection
and unchanged PHY registers. The rejected program/model are preserved under
`tools/fixtures/`. See [the architecture report](DSP_ARCHITECTURE_DISCOVERY.md)
for full-frame scoring and the negative 200,000-candidate search.

The sections below describe the original rejected experiment, including its
old eight-phase/twelve-frequency parameters and historical selection protocol.

Extends C5VRX by Twotoz and contributors and its existing PLL/tracking-demod
research: https://github.com/Twotoz/C5VRX. Official website and Discord invite:
https://twotoz.github.io/C5VRX/. Existing donor notices and GPL-3.0-only remain.

## Implementation

This is a recursive, quantized second-order phase/frequency loop, not a renamed
discriminator or the floating IQ40 PLL from MEGA_DEMOD_STUDY.md. The selected
model has eight phase states and twelve frequency states. Each pair updates
both from the wrapped difference between observed and predicted phase. The
output uses corrected frequency plus proportional phase error. It does not
retain the floating model's amplitude confidence weighting.

A 256-entry raw decode supplies eight phase tokens. A 768-entry transition
table maps `(previous phase/frequency state, current token)` to six DAC bits
and seven state bits. Together they occupy 1024 LUT16 entries / 2048 bytes.
Eight instruction slots implement four controller/worker pairs. The controller
emits duplicate DAC6, preserves state in unused bits and reads the next raw
pair; the worker addresses the next transition. Invalid startup states recover
through the decode plane. The first output pair is untrusted startup.

Q4/I4 IQ40 enters the existing raw32K ring. TX-only BitScrambler runs two
bundles per 50 ns: unique CVBS20 duplicated at physical DAC40. No per-sample
CPU work, transformed ring or DMA-boundary state reset is added. The first
byte of each pair participates, as in the current pair compiler; this is not
an all-acquired-sample estimator.

## Search and limitations

The initial phase16/frequency6 study screened 4096 configurations and was
negative. The expanded study screens 8192 configurations across phase counts
2/4/8/16/32 and frequency counts 48/24/12/6/3. It uses 24,576 short proxy cases,
sixteen longer selection candidates and freezes the best weak-objective
configuration. Strong regressions are reported, not gated out: the operator
requested an experimental low-signal PLL board test.

Configuration seed 7201; screen 7202; selection 7301/7302. Diagnostic 7401..7403
were reused during architecture comparison and are not independent final
evidence. The frozen model receives fresh nominal confirmation 8401..8403
and echo/fading confirmation 8501..8503 without reselection. Parameters are
pinned in `tools/pll96_model.json`; search never runs in CI.

Fresh nominal confirmation averages weak C/N 0/2/4/6 dB over three patterns,
four IQ RMS levels and three seeds. PLL96 output SINAD is 4.473 dB versus
OVP56 2.406 dB. Large errors fall from 109.85 to 49.48 per 1000; coherent
contrast rises from 67.06% to 80.45%. This does not establish RF sensitivity gain.
Worst strong-case loss is 9.418 dB, including 30-dB C/N. Under echo/fading,
weak SINAD is 0.052 versus OVP56 0.293 dB: no average SINAD improvement.
Per-C/N means including VLP56 and HC50 are in `pll96_cnr_comparison.csv`.
Clean-only per-case calibration is an offline scoring reference, not a live
servo. Physical fine-lane folding, whole video fields/chroma and goggle lock
remain outside these synthetic tests.

The source-driven test covers all 128 valid/invalid startup states times all
256 raw bytes, then a continuous stream, size, eight slots, two bundles and
duplicate physical DAC6. The IDF assembler/compiler builds the program.
These prove modeled dataflow/buildability; physical throughput, waveform
quality and menu-to-live transitions still need a board.

## Controls and board acceptance

OVP56 remains the default. `g` / SETUP DEMOD adds **PLL96 LAB**, saved value5.
Serial `p` selects PLL96 from other modes; from PLL96 it returns directly to
OVP56. Both directions save only the demod selection and reboot. Channel,
calibration, gain policy and native/manual ownership remain intact. Span75
observers, AFC actuation, flywheel, mask, idle raster and live LUT writers
remain gated. Boot diagnostics identify the known strong-signal regression.

Flashing preserves the saved selection: select PLL96 explicitly and confirm
`pipeline=PLL96 LAB`. Compare matched RF/channel/gain, strong then weak, fade
recovery and menu transitions. `p` provides OVP56 rollback. Record successful
flash and physical acceptance separately from build success.

## Reproduce

```sh
python v4/tools/detector_study/pll96_search.py --output /tmp/pll-search --configurations 8192
python v4/tools/detector_study/pll96_search.py --output /tmp/pll-confirm --confirm /tmp/pll-search/frozen.json
python v4/tools/generate_pll96.py
python v4/tools/test_pll96.py
cd v4
python tools/verify.py
idf.py build
```

Offline search needs NumPy/SciPy/Numba; pinned firmware generation and tests
use the standard library. Passing tests does not promote this lab candidate.
