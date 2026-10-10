# Receiver-aware IQ sequence reference

This extends **C5VRX by Twotoz and the C5VRX contributors**, specifically the
PAIR/EDGE, amplitude-aware particle and belief-compression studies at
`49b926908ca0aae204164949c9b76cbb9b5fc0b8` (PR #190).
[Canonical source](https://github.com/Twotoz/C5VRX),
[official website and Discord invite](https://twotoz.github.io/C5VRX/).
Existing author notices and GPL-3.0-only apply.

## Problem and scope

A receiver can lose useful video in its IQ interpretation before a goggle
loses sync. Stable synthesized sync alone does not recover missing picture.
This work implements an offline reference and controlled ablations to separate
observation loss, receiver-model assumptions and finite hypothesis capacity.
It does not add a selectable firmware demodulator or change the existing
raw-IQ DMA, gain, AutoFit, Fusion, DAC, menu or default settings. The alpha
firmware built for this PR contains the existing live demodulators.

This is a numerical estimator with fixed priors, not a search for thousands of
trained LUTs. It also is not an optimal recovery bound. Positive engineering
screens must not be described as additional physical RF range.

## Implemented estimator

`tools/dsp_search/iq_sequence.py` retains a population of alternative phase /
frequency trajectories. Each hypothesis also retains frequency slope, an
amplitude/noise regime, the complex receiver-filter memory and conditional
I/Q noise means and variances. All hypotheses process every acquired IQ byte.

The forward model is:

```
phase[k+1] = phase[k] + 2*pi*frequency[k] / 80 MHz
carrier[k] = amplitude * exp(j*phase[k])
received[n] = (h * carrier)[2*n] + noise[n]
observed[n] = actual_selected_ADC_bits(received[n])
```

`h` is the existing synthetic fifth-order Butterworth receiver, represented
with persistent filter state at the native 80-MS/s model rate. The measured
input remains 8-bit packed I-high/Q-low at 40 MS/s. Interpolation of frequency
between acquired samples is a model assumption; the physical tap/filter
ordering and ADC transfer remain unverified.

For a captured nibble, `cell_intervals()` builds the union of all analogue
intervals mapping to that code, including saturated ADC tails. It integrates
Gaussian probability and conditional moments over that union. It does not
assign one signed-cell centre to a folded code. Interval probabilities agree
with the existing independently tested ADC-cell integral; conditional moments
are checked against independently sampled quantization, including rail cases.

The coloured-noise variant uses an AR(1) approximation whose lag-one
correlation is derived from the known synthetic receiver impulse response:

```
noise[n] = rho * noise[n-1] + innovation[n]
rho = sum(h[k]*h[k+2]) / sum(h[k]^2)
```

The component noise state is updated with the quantized conditional moments.
This is Gaussian assumed-density filtering: it collapses each conditional
noise distribution to mean/variance, including aliases. AR(1) matches the
first noise correlation, not the complete fifth-order noise spectrum.
Consequently this is not an exact posterior of the synthetic channel, much
less an exact model of the undocumented physical receiver.

A contamination mixture prevents one improbable observation from permanently
eliminating every alternative. Default tempering is 1; optional power
tempering is explicitly generalized Bayes. Persistent amplitude/noise regimes
are inferred; neither the test C/N nor supplied gain is an inference input.
The amplitude grid includes a near-zero carrier hypothesis.

The optional delayed estimate follows actual resampling ancestry. For source
sample `s`, it only uses IQ through `s + lag`, and reports that availability
explicitly. It is not a shifted version of the causal estimate. The exercised
lag is eight raw samples, 200 ns. Unavailable outputs remain NaN; startup is
not filled with synthetic sync, and the tail does not wrap to the beginning.
The output conversion retains the existing reference's extra one-sample
publication delay and emits unique DAC6 values at 20 MS/s, repeated at 40 MS/s.
All of this is host processing, not proof of a legal BitScrambler schedule.

The `pair4411` ablation integrates the discarded second-byte bits: at the
second sample only the I/Q signs are observed. Changing its six discarded
bits cannot change any estimate, as checked by a dedicated test. The default
variant retains all sixteen acquired bits per 50-ns pair. Neither variant
claims access to the unacquired Q10/I10 stream.

## Comparison protocol

`iq_sequence_study.py` records its complete configurations, seeds, input
hashes, source hashes, calibration and decoder subset before confirmation.
All comparators receive the same hashed IQ. Failed and negative experiments
are retained under `docs/data/iq_sequence/`.

The main confirmation uses a separate seed, PAL zoneplates / NTSC checkers,
amplitudes 3 and 7 cells and C/N 2, 6, 13 and 30 dB: sixteen distinct 16,384-byte
records. The 512-particle variants are compared with EDGE, PAIR and the
existing particle reference at the same particle count. A separate development
run tests independent observations, receiver state, AR(1) noise and delay;
a development convergence check increases the hypothesis budget to 2,048.
These short records exercise horizontal sync and detail, not full fields.
Zero vertical misses in a record without a vertical interval proves nothing.

One **independent nominal clean record** supplies the shared gain/offset and
a constant latency for each decoder. Calibration is then frozen across all
confirmation records. No noisy-truth delay, per-record gain or porch-offset
fit is allowed. This differs from earlier per-record clamp screens; their
absolute numbers must not be compared as if protocols were identical.

The recorded `synthetic-fit` comparison supplies the synthetic deviation and
centre to all appropriate output mappings, including existing controls.
It does not supply them to IQ inference and does not demonstrate autonomous
physical AutoFit. `--calibration nominal` is a separate declared comparison.

Scores include waveform SINAD, missed and false sync, detail correlation,
active-video RMSE / large errors and burst integrity. The existing detail
metric covers the waveform; active-video errors are reported separately so
valid sync cannot be mistaken for recovered picture.

The additional transient scenario retains 65,536 raw samples (1.6384 ms) per
case and includes 80- and 180-us carrier losses, a 1.3x gain excursion and a
1.1-radian phase jump. Noise remains during signal loss. Recovery means three
consecutive horizontal pulses with matched width and timing, **not measured
goggle lock**. No synchronization pulses or previous lines are injected.

## Physical replay

`replay_iq_sequence.py` verifies the metadata, sample count, lane and FNV-1a
checksum before inference. It hashes each input and resets unknown state at
each capture start. It never concatenates separate snapshots across gaps.
Native-timing output arrays, uncertainty and source/availability indices can
be exported with `--save-arrays` for external comparison.

Four archived captures are included in `data/iq_sequence/captures/`: two from
the 2026-10-08 strong-static recording and two from its weak-static recording.
Each contains 8,190 bytes / 204.75 us at 40 MS/s, ultrafine lanes, 5865 MHz;
their original metadata/checksums are preserved. They predate later DCO changes
and have no known-video truth or calibrated attenuation. The assumed receiver
model is declared in the replay manifest, not inferred from a `BW40` label.
Replay reports outputs and uncertainty, not SINAD, recovered-picture
percentage, goggle acceptance or additional range.

## Recorded outcomes

Independent 512-particle confirmation, four cases per C/N. Each cell shows
mean waveform SINAD dB / total missed horizontal pulses:

| C/N | PAIR + fit | Existing particle | Receiver AR1 | Receiver AR1 + 200 ns | Receiver AR1, PAIR4411 bits |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 2.26 / 12 | 4.11 / 1 | 2.20 / 5 | 2.82 / 2 | 2.36 / 5 |
| 6 | 7.15 / 3 | 6.25 / 0 | 7.15 / 1 | 7.51 / 0 | 6.56 / 6 |
| 13 | 11.61 / 0 | 11.08 / 0 | 8.85 / 0 | 8.76 / 0 | 7.60 / 1 |
| 30 | 15.62 / 0 | 16.29 / 0 | 9.39 / 0 | 10.26 / 0 | 8.87 / 0 |

The 6-dB result is useful but limited: delayed receiver inference has zero
false syncs, versus PAIR's .438 per line and the existing particle's .188.
Its detail correlation is .714 versus PAIR's .733 / particle's .616. Active
RMSE is 23.44 IRE versus PAIR's 20.89 / particle's 26.22. Thus the waveform
gain over PAIR is not a blanket improvement in active picture.

At 2 dB the existing particle remains better. At 13/30 dB the receiver-aware
variants lose substantial picture quality and fail the existing strong-SINAD
guard. More observed bits help several comparisons, especially at 6 dB, but
are not sufficient to make this approximate finite estimator universally
better. **No overall replacement qualifies.**

On the paired development inputs, increasing the particle budget from 512 to
2,048 improves delayed-receiver strong SINAD from 9.91 to 13.42 dB, but the
existing 2,048-particle reference still reaches 18.17 dB. This demonstrates
finite-inference sensitivity, not convergence to optimal recovery. The richer
receiver/noise model by itself does not establish a better decoder.

Transient comparison: delay-aware three-pulse recovery in microseconds after
the two carrier losses; a dash means no qualifying run in the available
512-us search window, not permanent loss of goggle lock.

| Standard | EDGE | PAIR | Existing particle | Receiver AR1 + 200 ns |
| --- | --- | --- | --- | --- |
| PAL | — / — | — / — | — / — | 48.1 / 88.1 |
| NTSC | 33.9 / 5.9 | 97.4 / 69.4 | 33.9 / 260.1 | 33.9 / 69.4 |

Across these two stressed records, receiver-aware SINAD is 1.57 dB versus
PAIR's 2.28 dB, with ten missed horizontal pulses each. The useful PAL
recovery is therefore not an overall picture-quality or sensitivity win.
No injected sync, old-line copy or known raster clock contributed to recovery.

The four physical fixtures all pass checksum/format checks and replay with
finite post-startup output and uncertainty. They do not establish a picture
benefit without matched truth or a current-board attenuation experiment.

The reproducible negative conclusion is to retain this as an offline
reference, not compress/promote it as a new C5 firmware demodulator. Better
guided proposals or conditional Gaussian mixtures, a more faithful noise
model and inference of video structure remain separate experiments; neither
their benefit nor a legal realtime implementation is demonstrated here.

## Hardware boundary and follow-up

The current shared LUT provides 2 KiB and two lookups per 50-ns span. The new
reference has explicit floating-point filter/noise state per hypothesis and
resampling history. At 512 hypotheses and an eight-sample delay its persistent
arrays alone occupy approximately 116 KiB, before output arrays, work copies
or existing receiver memory. It cannot be directly installed as the current
LUT or a 40-MS/s CPU loop. More elapsed latency does not create additional
BitScrambler lookups or solve the historical active-MAC SRAM access failure.

The next deployment step requires a compact inference design that retains
its measured benefit and proves legal addressing, sustained throughput and
state continuity. Existing failed ROM projections remain negative evidence;
this work does not invalidate them or prove every compact architecture
impossible. An external DSP/FPGA is a conditional architecture alternative
already investigated in `RESEARCH.md`, not a selected new component here.

For physical acceptance, feed the same known moving video into a controlled
attenuation / fade experiment. Record actual receiver gain, DCO state,
frequency, lane, demod and CVBS output alongside any IQ captures. Present
H/V timing, moving detail, burst preservation, loss/recovery and the actual
goggle picture separately. Independent 205-us snapshots cannot establish a
continuous dropout trajectory. A longer contiguous raw capture requires a
proven capture route; repeated USB snapshots or relaxed ring-copy guards
do not supply one. Prior capture/refusal gates must remain intact.

## Reproduction

Use a research Python environment with NumPy, SciPy and Numba; the exercised
versions are pinned in `tools/dsp_search/requirements-iq-sequence.txt`. From
`v4/tools/dsp_search`, with `PYTHONPATH` also containing `v4/tools`:

```sh
python -m unittest test_iq_sequence test_iq_sequence_replay -v
python iq_sequence_study.py --output /tmp/iq-confirmation \
  --names EDGE+AF PAIR+AF 'Existing particle' 'IQ receiver AR1' \
  'IQ receiver AR1 lag8' 'IQ receiver AR1 pair4411'
python iq_sequence_study.py --output /tmp/iq-transient \
  --names EDGE+AF PAIR+AF 'Existing particle' 'IQ receiver AR1 lag8' \
  --scenario transient --size 65536 --cnr 6 --per 2 --amplitudes 3 --seed 5300191
python replay_iq_sequence.py --output /tmp/iq-replay \
  --input ../../docs/data/iq_sequence/captures/strong-01.iq \
  ../../docs/data/iq_sequence/captures/strong-02.iq \
  ../../docs/data/iq_sequence/captures/weak-02.iq \
  ../../docs/data/iq_sequence/captures/weak-03.iq --save-arrays
python validate_iq_sequence_evidence.py --evidence ../../docs/data/iq_sequence
```

Every run requires a fresh output directory. Static runs recorded before the
transient extension use the same estimator/scoring arithmetic; source hashes
identify the script revision used, and extra recovery fields are absent. The
original confirmation/convergence script is archived byte-for-byte under
`data/iq_sequence/source_versions/`, with its verified source fingerprint.
The evidence validator accepts LF/CRLF-equivalent Python source text for
Windows/Linux reproduction. Raw IQ checksums remain byte-exact; scoped Git
attributes prevent newline conversion of the binary capture fixtures.

## Primary theory and prior project research

- Tam and Moore, [Gaussian-sum phase/frequency estimation (1977)](https://users.cecs.anu.edu.au/~john/papers/JOUR/050.PDF): retaining several hypotheses can extend a PLL threshold in the paper's model. It supplies no C5/PAL gain guarantee.
- Tam, Tam and Moore, [fixed-lag FM demodulation (1973)](https://users.cecs.anu.edu.au/~john/papers/JOUR/035.PDF): the studied approximation can improve error while retaining the underlying demodulation threshold. Delay alone is not the solution.
- Collings and Moore, [adaptive HMM estimation in fading channels (1994)](https://users.cecs.anu.edu.au/~john/papers/PROC/056.PDF): joint message/channel estimation; the present folded-quantizer/noise approximation differs.
- [Adaptive particle study](ADAPTIVE_PARTICLE_STUDY.md), [belief compression](BELIEF_ROM_STUDY.md) and [PAIR range study](PAIR_RANGE_STUDY.md): original C5VRX references, negative results and calibration boundaries.
