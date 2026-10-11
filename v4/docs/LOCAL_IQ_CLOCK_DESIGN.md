# Direct-IQ timing model: local C5 research design

Extends C5VRX by Twotoz and contributors, https://github.com/Twotoz/C5VRX,
https://twotoz.github.io/C5VRX/ (official Discord invite there). Original
GPL-3.0-only licensing and research provenance apply. Base7491a66. Local
research only: no firmware promotion or flashing. The operator authorized
publication in a new PR on 2026-10-11 after the local experiments.

## Evidence and scope

`tools/dsp_search/local_iq_clock.py` estimates line timing directly from past
received IQ. It does not receive true CVBS, carrier offset, CNR, standard,
clean samples, signal levels or initial line phase. Nominal PAL/NTSC periods,
40MHz sample rate and4.7us H width are declared design priors. Known camera
period drift is not supplied or handled yet. This is a timing observer for a
hierarchical receiver, not a complete replacement IQ-to-CVBS demodulator.

The weighted-product/synchronous-averaging idea is established research; the
[television cyclostationarity patent](https://patents.google.com/patent/US20100157066A1/en)
discusses synchronous averaging and delay-multiply detection. Its TV detection
results and broadcast modulation assumptions do not prove WBFM FPV decoding
or C5 feasibility. Historical C5VRX weighted-correlation demods and raster
flywheels are also prior work. The specific sparse received-IQ observer and
its resource design are the local experiment.

Initial seed7300190 fails broadly: stride64 is commensurate with the2560-byte
PAL period and revisits only40 phases; blank histogram bins and a relative
porch-only score also let picture patterns impersonate sync. Both failures
and their source are retained in `data/local_joint/iq-clock-reference`.
Seed7400190 uses stride63 and absolute low-frequency contrast plus a small
porch term. Noise and some picture patterns still produce false timing.

The detector is then frozen with a normalized score threshold0.23. Independent
seed7500190 has12 signal cases and16 pure-noise controls, all randomly cropped
in line phase. It does not acquire the -2/0dB cases reliably; one declared-lock
4dB PAL case has bad timing. Therefore this is not a general improvement over
U85Q. It does recover timing in the off-centre echo/fade cases where both
U85Q goggle models fail H lock.

Fresh seed7600190 repeats that specific stress class16 times (eight PAL and
eight NTSC), CNR6dB, deviation1.2, carrier offset-2MHz relative to the nominal
1MHz centre, echo and fading. Each estimate uses only the first2ms of a6ms
crop; subsequent4ms supplies timing labels after the estimate is frozen.
All16 select the correct standard and declare lock. All scored held-out lines
are within500ns after removing the simulator's declared500ns transmitter FIR
delay (81 taps at80MHz, not a per-case fitted offset); case p95 errors range
19–299ns. The raw uncompensated within500ns mean is only16.7%; real pipeline
latency must be established separately. U85Q's ideal and peak H-lock fractions
both average0.953% in those cases. These compare a timing observer with a
full goggle-lock path; they do not mean100% picture versus0.953% picture after
an implemented repair. The existing U85Q baseline uses clean-oracle
calibration, whereas the observer uses received IQ only.

The16 fresh noise-only controls produce zero declared locks. This small null
sample does not bound rare false locks, other interference or false VTX
detection. No range gain in dB, metres or flight acceptance is established.
Results and exact executed sources are in `iq-clock-validation` and
`iq-clock-stress-validation` under `data/local_joint`.

## Estimator

For adjacent captured samples a,b, form C=b*conj(a). Accumulate these complex
products by trial line phase before taking a frequency-like projection. Small
IQ products contribute little; a noisy near-origin sample does not first
become a large wrapped-angle increment. Thermal-noise correlation introduces
a bias, and folded ADC lanes can still corrupt products; this is not an exact
unbiased FM estimator.

Take one pair every63 raw bytes (~634921 pairs/s,2 raw bytes/pair). For each
nominal standard, assign the product to128 phase bins with linear interpolation.
Rotate each bin's mean product by the conjugate of the received global mean.
The imaginary part represents frequency contrast with the received average,
avoiding an absolute CFO threshold. Score a4.7us negative excursion against
the global reference, plus0.2 times front/back-porch contrast. Select the best
standard and phase; use bounded sub-bin parabolic interpolation. Normalize
the score by received IQ power before the fixed0.23 lock threshold.

The simulator uses floating point and exact modulo. A C5 implementation must
replace these with proven fixed-point phase accumulators and integer sums;
host speed is not a C5 throughput measurement.

## Concrete C5 mapping to validate

Keep RX40/raw32K and the U85Q TX-only BitScrambler unchanged. The CPU observer
reads sparse pairs from completed RX regions, using monotonic sample-time tags
and rejecting windows that could have been lapped. The2ms integration spans
several raw-ring laps, but products are accumulated online; no80KiB snapshot
or frame buffer is required on the board. A missed/lapped region must produce
an explicit gap, not a fictitious contiguous timestamp. Ring-offset reads
alone do not establish wrap count after arbitrary preemption.

Decode each nibble into the odd integer2*q+1. Four integer multiplications
produce4*Re(C),4*Im(C); magnitudes are at most450. An8-bit interpolation weight
keeps even a worst-case2ms bin sum below2^31. Two standard hypotheses need
128 bins x(real,imag,count) x4 bytes x2 =3072 bytes, plus global sums and
small clock state. Two uint32 phase accumulators replace per-pair modulo;
the increment corresponds to63 input samples per update. Fixed-point phase
error and PAL/NTSC selection require separate numerical verification.

Sparse raw reads are about1.27MB/s. The target total acquisition budget is
at most60 CPU cycles/pair,38.1M cycles/s (15.9% of240MHz), plus bounded
histogram scoring. This is an engineering budget, not measured or proven.
Use rolling rectangular sums to evaluate128 phase candidates rather than
128 full histogram scans. Once locked, update only the selected hypothesis
and refine phase/period from fresh blocks. Timing under AGC, USB, menu,
cache/bus contention and actual task preemption remains an acceptance gate.

## Connection to actual video output

The output of this observer is a timestamped H-clock hypothesis and confidence,
not DAC samples. Preserve live U85Q active video initially. Conditional sync
repair would require a separate V/field model, burst/porch handling, standards
validation and signal-level acquisition from received data. Never overwrite
every H pulse merely because an argmax exists; a false clock could destroy an
otherwise valid feed. The broad-study failure is a real warning about this
model's evidence, not a hypothetical acceptance issue.

`local_sync_words.py` proves only that a precomputed IQ sequence can establish
a known U85Q state and emit exact selected DAC codes. Its example codes4/25
have no proven voltage/CVBS acceptance and must not be assumed suitable for
every carrier offset or deviation. Actual receive-to-DAC timing must align
the repaired pulse with the unchanged active-video path.

The current driver-owned cyclic chain cannot be assumed safe to splice.
Line-commensurate rings reduce nominal descriptor counts but change the
current32KiB invariant, shorten lead time, and do not track camera drift.
An implementation therefore still needs documented descriptor ownership /
fetch bounds or a physically validated alternate transport. The historical
in-flight menu splice is not revived by this timing result.

The goal remains unachieved: no complete C5-executable better demodulator,
continuous repaired CVBS, colour acceptance or measured range improvement
exists yet. The positive evidence narrows the next experiment to hierarchical
use of surviving IQ timing, rather than more phase-gain tweaks.

## Integer streaming implementation and corrected CPU estimate

`local_iq_clock_integer.py` implements the per-pair loop using uint32 phase,
8-bit interpolation weights and int32 histogram accumulation. It reconstructs
all16 frozen seed7600190 inputs and verifies their hashes before comparison.
The resulting clock differs from the original by at most2.20ns; all adjusted
held-out timing checks remain within500ns. Chunk sizes1,2,63,64,4092,8191 and
80000 produce identical accumulators. The implementation proof replays existing
cases; it is not an independent performance confirmation. Histogram scoring
still uses the floating reference on128 bins outside the sample loop.

The standalone `local_iq_clock_stream.c` additionally carries an A byte when
its paired B byte arrives in the next chunk. Its two-hypothesis histogram is
3072 bytes; sums, phase, byte carry and offsets add a small fixed amount. A
sample-count guard bounds the accumulator at1270 products. The caller must
close the integration window and handle missing/noncontiguous data separately;
the C prototype does not establish ring ownership or timestamps.

The actual cached Espressif IDFv6.0.2 riscv32-esp-elf-gcc accepts the standalone
source with -O3 -march=rv32imc -mabi=ilp32 -Wall -Wextra -Werror. This uses
integer instructions supported by the C5 target toolchain, without changing
firmware. Frozen cross-assembly is in `iq-clock-integer-proof`. Host gcc14
execution matches the Python integer histograms, sums and final phases byte
for byte in14 tests, covering random IQ and maximum products across seven
chunk sizes. This is C semantics and cross-compile evidence, not board timing.

The cross-assembly corrects the earlier optimistic60-cycle budget: the steady
path contains122 instructions, including14 multiplications,19 loads and18
stores. At stride63, the instruction count alone corresponds to32.3% of a
240MHz CPU **if** one instruction retires per cycle. It excludes multiply
latency, stalls, scoring, contention and preemption; actual C5 cycles remain
unknown. The60-cycle target is not met by this implementation.

To reduce work without discarding half the statistical evidence, a new design
samples every127 bytes and integrates4ms. This yields1260 products instead of
1270; the integration is online and does not require a160KiB snapshot. The
same122-instruction path at314961 pairs/s corresponds conditionally to16.0%
CPU before the excluded costs. Raw sparse reads fall to about0.63MB/s, and
the histogram remains3KiB. Longer integration increases acquisition latency
and worsens sensitivity to an unmodelled camera-clock drift; it is not free.

Independent seed8000190 tests16 new cases in the same off-centre echo/fade
stress class. All select the correct standard and declare lock; all adjusted
held-out timing checks pass500ns. The held-out interval is now2ms, not the
earlier4ms, so these are not identical validation horizons. The16 new noise
controls have zero declared locks, which still does not establish a rare
false-lock bound or broad-channel acceptance. General low-CNR, clock drift,
VBI/field and interference behavior require further validation.

The stride127 integer replay matches the float clock within2.77ns and retains
all16 timing successes. Its C variant also cross-compiles and passes14 separate
host chunk-equivalence tests. Frozen sources, hashes, replay and static budget
are in `iq-clock-half-rate-integer-proof`; fresh performance cases are in
`iq-clock-half-rate`. No physical execution time or complete CVBS benefit is
claimed from these implementation proofs.
