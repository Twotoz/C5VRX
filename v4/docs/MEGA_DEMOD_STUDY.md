# Occupancy-aware LUT search and multi-theory FM benchmark

Extends **C5VRX by Twotoz and the C5VRX contributors**, the PR185/PR186
research, adjacent/trajectory/PLL studies and Louis Hitchcock's staged gain
recovery. Canonical source: https://github.com/Twotoz/C5VRX.
Official website and Discord invite: https://twotoz.github.io/C5VRX/.
Existing author notices and GPL-3.0-only apply.

## Goal and boundaries

The operator reports a good strong-signal picture that becomes grey/grainy
and then disappears at low input. The objective penalizes weak-signal noise,
large errors and coherent contrast loss, with independent strong-signal
guards. No physical RF sensitivity or goggle-lock gain follows from these
synthetic results. No global optimum or previously unknown demodulation law
is claimed. Existing demod principles are tested and combined experimentally.

## Hardware-constrained search

512 architecture/initializer attempts plus retained-encoder table variants
produce 544 distinct encoder/table/layout candidates. All 544 receive a
matched filtered-video screen; 42 survive family/context quotas. Four
encoders, including OVP56, receive two physical-DAC filtered table refits
each. Conservative integer blends and the references make 61 selection
models. All retained schedules fit LUT8/2 KiB, eight slots and two lookups
per 50 ns, raw IQ40/raw32K/TX-only, CVBS20 duplicated at DAC40.

The offline solver fits the actual passive-DAC conductance levels, the finite
5-MHz output filter and 768 clean absolute-level constraints. Its adjoint
and fixed-bin discrete optimum are independently tested. Integer rounding
does not prove a global filtered/integer optimum. The encoder search is local
and covers two schedule families, not every legal C5 architecture.

Training seeds: 2101/2102. Screen: 2201. Selection: 2301/2302. The winner
is frozen before final 2401/2402/2403 and DC/CFO/deviation/skew confirmation
2501/2502/2503. The objective is:

```
mean(SINAD - .003 * errors_per_1000 - .015 * abs(100 - contrast_percent))
```

Weak C/N is 0/2/4/6 dB. Selection strong C/N is 14/18 dB. Each strong
case may lose at most .25 dB SINAD, .5 large errors/1000 and 1 percentage
point contrast relative to OVP56; clean mean levels must stay within one
normalized full-scale DAC unit. Confirmation uses unseen level/radius/CFO
combinations. A failed final vetoes promotion; it never chooses a runner-up.

The best eligible candidate is a 25% integer blend of the regularization-1
OVP56 refit with the original OVP56 table. It changes 375 of 1,568 map entries.
It gains only 0.0142 dB mean weak output SINAD in the final and 0.0196 dB in
DC/CFO/deviation/skew confirmation. Large errors fall by 0.162/0.236 per
1,000 respectively; contrast decreases by 0.072/0.102 percentage point.
Worst strong losses are 0.086/0.101 dB; independent clean-level error is
0.083 normalized DAC unit. This is a tiny tradeoff, not a solution to the
operator's visible dropout. The model is pinned for reproduction, with no
firmware default change or board acceptance.

## Theory families

With received complex IQ `z[n]`, phase `phi`, and `arg` the principal angle:

| Family | Estimator / search | Unique configurations |
| --- | --- | ---: |
| Delay discriminator | `arg(z[n] conj(z[n-L]))/L`, confidence and smoothing | 15,000 |
| Cross-product | normalized imaginary product, arcsine or ratio approximations | 15,000 |
| Weighted correlation | phase of a weighted window of complex delayed products | 20,000 |
| Phase slope | amplitude-weighted local regression of unwrapped phase | 15,000 |
| Second-order PLL | predicted phase error, proportional/integral correction, confidence | 15,000 |
| Kalman-style tracker | scalar frequency random walk with received-amplitude noise weighting | 10,000 |
| Periodogram | finite-window constant-frequency likelihood approximation and interpolation | 1,000 |
| Hybrid | amplitude/coherence/agreement-weighted short and windowed estimates | 9,000 |

These are eight implemented families and 100,000 distinct parameter
configurations, not 100,000 distinct theories. Kalman/likelihood names describe
their assumptions, not statistical optimality for arbitrary analog video.
All receive the same raw inputs, passive DAC quantization and output filter.
The floating/recursive estimators process acquired IQ40 with unique40 output;
they are offline references with no demonstrated C5 realtime schedule.
Fixed adjacent40, adjacent-pair20, endpoint50 and pair20 versions of frozen
theory winners separately check output geometry. Pair20 output alone does
not make an all-sample/stateful formula fit two BitScrambler lookups.

## Valid experiment protocol

The valid run uses parameter seed 2601, short-screen signal seed 4701,
selection 6301/6302, frozen finals 6401/6402/6403 and channel confirmation
6501/6502/6503. A further high-C/N veto uses 6601/6602/6603. Earlier runs
with unsigned IQ arithmetic are invalid. A later selection also had ambiguous
case identities across nominal/stress scenarios; those downstream results are
invalid. Its unaffected 100,000-configuration screen is reused with kernel,
parameter and CSV hash checks. Corrected selection and all confirmations use
fresh seeds. Invalid results are excluded from the scientific counts.

Each of the 100,000 configurations gets three weak and three strong
1,024-sample proxy windows: 600,000 short tests. The proxy windows cover
active synthetic video and are not whole-frame/sync tests. The best three
per family receive longer nominal and DC/CFO/deviation/skew selection tests.
The strong proxy gate discourages candidates that win by discarding detail.
Strong contrast may improve toward 100%; increased contrast error/overshoot
is limited to one percentage point. SINAD/error guards remain .25 dB/.5 per
1,000. One winner per family is frozen before the final seeds, even when
the entire family is ineligible; failed families remain explicit negatives.

Each candidate's delay/scale/offset is fitted on a noiseless counterpart,
then frozen before scoring noisy output. This differs from the LUT search's
common OVP56 calibration; do not subtract numbers between those experiments.
Noisy contrast loss and large-error tails remain visible. Clean calibration
is an offline measurement reference, not an implemented live CVBS servo.

The independent channel tests add declared static echoes (3/8/15 samples,
complex amplitudes .35/.5/.25), sinusoidal envelope fading of depth .5/.25/.75
over 150 us, and unseen DC/skew. These are falsification scenarios, not a
measured RF/environment model. Full PAL/NTSC fields/chroma, physical fine-lane
folding, PHY gain/noise-figure/transient effects and real DAC/goggle response
remain outside the model. A host winner is not flashable firmware acceptance.

## Frozen results and hardware decision

The valid experiment contains 600,000 short proxy tests, 3,696 longer selection
cases, 3,456 final cases and 1,512 echo/fading cases. A further 1,296 cases test
24/30/40-dB C/N. These are short synthetic sequences, not full video fields.

| Frozen method | Final weak SINAD (dB) | Large errors/1000 | Contrast (%) | High-C/N worst SINAD loss vs OVP56 (dB) |
| --- | ---: | ---: | ---: | ---: |
| OVP56 | 2.434 | 109.44 | 67.68 | 0.000 |
| WVP56 LUT blend | 2.449 | 109.26 | 67.59 | 0.145 |
| Weighted phase slope | 2.765 | 77.92 | 83.25 | 1.163 |
| Seven-sample periodogram | 3.410 | 80.25 | 87.08 | 1.078 |
| Second-order PLL | 5.477 | 38.02 | 84.05 | 2.467 |

The periodogram was the eligible overall selection winner, frozen before final
testing. It improves nominal weak SINAD by 0.976 dB, but loses 0.810 dB against
OVP56 in the weak echo/fading aggregate and fails the added high-C/N guard.
PLL improves nominal weak SINAD by 3.043 dB and reduces large errors by 65.3%;
it also improves the weak echo/fading aggregate by 2.766 dB. It already fails
the selection strong-signal guard and later loses up to 2.467 dB at high C/N.
Neither is a general replacement. No runner-up is selected after these vetoes.
All eight frozen theory families fail the high-C/N guard. The only retained
hardware-fit change offers a tiny improvement. Firmware defaults stay unchanged.

### Testing PLL with the C5

The frozen PLL is a received-amplitude-weighted second-order phase/frequency
tracker. Its exact parameters are retained in `models/theory_hypotheses.json`.
It extends the existing tracking-demod research, not an independent discovery.
Unlike a two-endpoint LUT, it needs updated phase and frequency state after
every acquired IQ sample. No continuous C5 implementation or throughput proof
exists; a slower control observer is not a live PLL video demodulator.
See `RESEARCH.md`, “Full PLL / tracking demodulator,” and the legacy
`research/realtime-feasibility.md` before changing the architecture.

The next useful board experiment is to record actual packed Q4/I4 snapshots
at matched strong/weak RF levels and replay OVP56 and this frozen PLL on the
same samples. `replay_c5_iq.py` prepares that comparison without inventing
SINAD or contrast scores from an unknown real picture. Snapshot replay tests
the estimator on real RF data; it does not prove gapless throughput, live
goggle lock or uninterrupted state across captures. A selectable live PLL
requires a separately demonstrated stateful hardware schedule. This study
does not flash a mode labelled PLL while running a different estimator.

## Reproduce

Offline NumPy/SciPy are required; the theory kernels additionally use Numba.
The run used Python 3.13.15, NumPy 2.5.2, SciPy 1.18.1, Numba 0.68.0 and
llvmlite 0.50.0, with `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`.
Firmware generation stays standard-library-only and never runs this search.

```sh
python v4/tools/detector_study/test_weak_signal_sweep.py
python v4/tools/detector_study/test_weak_signal_search.py
python v4/tools/detector_study/test_demod_theories.py
python v4/tools/detector_study/weak_signal_search.py --output /tmp/weak-model --variants 512
python v4/tools/detector_study/mega_demod_bench.py \
  --output /tmp/mega-screen --configurations 100000 \
  --lut-winner /tmp/weak-model/frozen.json --strong-screen --seed-offset 2000 --screen-only
python v4/tools/detector_study/mega_demod_bench.py \
  --output /tmp/mega-demod --configurations 100000 \
  --lut-winner /tmp/weak-model/frozen.json --strong-screen --seed-offset 3500 \
  --reuse-screen /tmp/mega-screen/screen.csv
python v4/tools/detector_study/phase_controls.py \
  --output /tmp/phase-controls --frozen-theories /tmp/mega-demod/frozen.json \
  --frozen-lut /tmp/weak-model/frozen.json
python v4/tools/detector_study/strong_signal_confirmation.py \
  --output /tmp/high-cnr --frozen-theories /tmp/mega-demod/frozen.json \
  --frozen-lut /tmp/weak-model/frozen.json
```

Evidence directories are new per run. Protocols and winners are written before
final evaluation. Screening CSVs stream all configuration parameters/scores;
selection/final/channel CSVs retain individual seed/scenario measurements.
Summaries distinguish proxy tests, complete signalled cases and theory counts.
These are a reproducible search within declared models, not a proof of the
best analog demodulator possible.
