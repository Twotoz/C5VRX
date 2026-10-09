# Ω belief-state demodulation experiment, PR #190

This extends C5VRX by Twotoz and the C5VRX contributors. Canonical source:
https://github.com/Twotoz/C5VRX ; official site and Discord invite:
https://twotoz.github.io/C5VRX/ . Audit started at PR head
`dd1db7a08b12d0177a06b73cd24b0e7e3cc181c4`. Before publication, PR head advanced
to `055a910aeac614ac26040cd37de8a906ca3ad675`; its untrained-PAIR
negative experiment and study addition are preserved. They change neither
firmware nor these benchmark controls.

**Experimental research, not a demonstrated replacement or global optimum.**
The hypothesis is that a learned recurrent compression of a multimodal
posterior can retain useful uncertainty without assigning explicit confidence
bits to a conventional phase/frequency lattice. The scripts actually implement
and evaluate this hypothesis; they do not rename a PLL or blend EDGE/PAIR.
See the result section below for the measured acceptance verdict. No merge,
new default, flight result or RF sensitivity improvement is claimed.

## Audit and evidence boundaries

The current transport is MODEM_DIAG -> PARLIO RX40 -> raw32K cyclic DMA ->
TX BitScrambler -> PARLIO TX40 -> passive DAC6. RX acquires **40 MS/s** of
four selected bits of I and Q, not all physical ADC bits or every 80-MS/s
modem sample. Within each acquired 50-ns span, two input bytes are available.
Existing eight-slot programs repeat four two-bundle spans. One bundle reads
and writes 16 bits and addresses the observation lookup; the next addresses
the transition lookup. The previous transition result produces `[D,D]` at
40 MHz, or 20 million distinct codes/s. There is a one-span pipeline delay.
The ROM wrap and state continuity come from the full eight-slot program, not
a CPU reset at a DMA boundary.

`compile_overlay.py`, `bs_model.py`, `overlay_fsm.py`, actual generated PAIR,
EDGE and RANGE32 source, `video_transport.c`, `video_autofit.c`,
`fusion_demod.h` and the Espressif v6.0.2 assembler/target description are the
implementation sources. `bs_model` explicitly models the established C5
LUT16 address at O16..25. The generic documentation's MSB-address wording is
not substituted for this project-specific mapping. Its source interpreter
proves dataflow, **not** clock closure, FIFO occupancy or analog compliance.

The shared RAM is 2048 bytes: 1024 LUT16 words. For b observation bits and
s=10-b state bits, the packing is:

```
word = dac6 | (next_state << 6) | (observation_token << (16-b))
transition_address = (old_state << b) | observation_token
```

The low and high fields are disjoint because 6+s+b=16. There is ONE RAM,
not a decoder RAM plus an independent transition RAM. All 1024 addresses
can contain both a decoder token and a transition. The decoder address
retains ten selected IQ bits across A/B. Allocation candidates are:

| states | observations | next-state bits | token bits | table words |
|---:|---:|---:|---:|---:|
| 256 | 4 | 8 | 2 | 1024 |
| 128 | 8 | 7 | 3 | 1024 |
| 64 | 16 | 6 | 4 | 1024 |

The experiment compares 4411, 3322 and 2233 bit layouts, in I_A,Q_A,I_B,Q_B
order. Any unsupported layout is rejected by the actual compiler. A/B do
not both survive at full 8-bit resolution. The first lookup compresses the
10 retained bits further to b bits; the teacher sees both complete bytes.
That difference is reported as part of the compression gap, not hidden.

The passive DAC resistor model is nonuniform. Score decoding uses the
existing `DAC_VOLTS` and goggle reconstruction filter. Frequency-to-code
conversion retains the project's established nominal transfer. Fixed output
levels do not guarantee that sync occurs at the right time: a constant or
incorrect frequency estimate can output electrically valid but useless CVBS.

## What the previous models actually establish

EDGE's low-gain second-order state tracker produces frequency state rather
than a large observation innovation. Frequency history protects plateaus,
including real sync, from isolated phase evidence. Its bandwidth, coarse
phase representation and output reconstruction attenuate chroma/detail.
PAIR's learned per-address phasors retain more phase resolution and useful
information from both acquired samples. Its faster correction/output can
translate uncertain phase evidence into large FM excursions and false sync.
Near-origin IQ, winding ambiguities, lane folding and tracker/grid errors can
all contribute; a click counter alone cannot identify their cause.

Quantization is not equivalent to ordinary saturation for the fine lanes.
For ultrafine, bits {9,6,5,4} preserve sign and wrap the low three bits
repeatedly within each sign half. Treating nibble 7 as simple positive
clipping or decoding one amplitude interval per code is an incorrect
likelihood model. DC correction, gain ownership and sampling phase are
independent controls; firmware demodulation cannot repair samples that were
never acquired or were captured during a bus transition.

Both scoring corrections in `PAIR_RANGE_STUDY.md` matter. The software
teacher must have the same one-span DAC timing; model-dependent group delay
must also be accounted for. Existing `search_edge.clamp` reselects latency
within ±8 raw samples on noisy truth. We reproduce that corrected metric
but additionally choose one lag on the model's clean counterpart and freeze
it for the corresponding noisy stream. Only porch DC is restored afterward.
Thus a noisy output cannot select its own favorable alignment.

Negative results already retained in the PR include a state-conditioned
phase decoder, phase-relative tokens, Kalman-style per-sample reliability,
25-ns single-lookup trackers, 75-ns cascades, confidence/token splits, CVT
output blends, simple multipliers, extra-fine lanes, direct entry polish and
misaligned apparent teacher gains. We do not claim these as new inventions.
Phase8 has differential wrapping/quantization errors rather than a complete
posterior. RANGE32 carries a compact single tracking hypothesis. Fusion is
a control policy selecting programs; it is not multi-hypothesis Bayesian
fusion of every IQ observation. The comparison called **Fusion oracle
policy** in the new benchmark selects EDGE below 13 dB and PAIR otherwise
using test C/N. It is explicitly an oracle comparator, not a measurement of
the deployed phase-C/N estimator, hysteresis, program-swap seams or reloads.

## Mathematical foundation and literature assessment

Received samples follow z=A exp(j phi)+w, but likelihoods must integrate
across all analog intervals mapped to the observed folded nibble. With
a latent noise class r and discrete phase phi_p:

P(q_I,q_Q | r,p) = P(q_I | A_r cos(phi_p)) P(q_Q | A_r sin(phi_p)).

For a component code c:

P(c | mu,sigma) = sum over ADC cells a mapped to c of
[Phi((upper_a-mu)/sigma)-Phi((lower_a-mu)/sigma)].

The end cells extend to infinity to represent ADC saturation. The compiler
and simulator do not document the actual RF ADC transfer/noise distribution.
This likelihood is exact for **`iq_lanes.quantize` plus independent Gaussian
noise**, not an experimentally calibrated likelihood of every physical C5.
Monte Carlo tests exercise folding on both signs, including beyond the
near-origin range and all three lane profiles.

The robust observation is `(1-epsilon) P(q|r,p) + epsilon/256`. It preserves
support for all observations and bounds catastrophic overconfidence. This
uniform contamination component is a model of atypical samples, not proof
that correlated Wi-Fi interference is IID uniform noise.

The causal sum-product recursion is:

b_n(r,f,p) proportional to L(q_n|r,p) sum_{r',f',p'}
T(r,f,p | r',f',p') b_{n-1}(r',f',p').

It retains multiple frequency/phase hypotheses simultaneously; there is no
phase-detector error feeding a tuned PLL. A mixture of normalized Gaussian
frequency transitions uses a narrow 0.35-MHz component and a wide 2.4-MHz
component. The wide component admits real video edges/chroma. Phase advances
at **40 MS/s**, with interpolation on the circular phase grid and a small
phase-diffusion floor. Three persistent noise hypotheses (priors corresponding
to 1, 8 and 23 dB at nominal total IQ occupancy) are marginalized with slow
migration. These are hypotheses internal to the model; the filter is never
told a case's true C/N. Amplitude is coupled to noise class at a nominal
occupancy, a simplifying assumption tested with varied amplitudes and gain
steps. This is not a full estimator of arbitrary independent amplitude/DC,
colored noise, multipath or every VTX parameter.

The filter emits one posterior frequency mean after sample B per span, then
quantizes to DAC6 and duplicates it. Under squared frequency reconstruction
loss, nearest transfer-code to the posterior mean is the discrete Bayes-risk
action. It is **not** the optimum for arbitrary perceptual/CVBS loss, actual
resistor nonlinearity or goggle synchronization. Heavy false/missed-sync costs
rank candidates offline; they do not supply a raster or sync label at runtime.
A skewed sync-specific Bayes loss might help, but can hallucinate sync or
suppress legitimate black/chroma and needs independent qualification.

The following primary sources guided the design; they do not demonstrate a
C5 video benefit on their own:

- [Espressif BitScrambler driver and assembly](https://docs.espressif.com/projects/esp-idf/en/v6.0.2/esp32c5/api-reference/peripherals/bitscrambler.html)
  and [C5 TRM](https://documentation.espressif.com/esp32-c5_technical_reference_manual_en.pdf).
  Use the actual assembler for source-route constraints. Counters and input
  sources cannot be freely combined in any bundle.
- [Shayovitz and Raphaeli, Tikhonov-mixture phase tracking](https://arxiv.org/abs/1306.3693).
  Circular mixture reduction preserves multiple trajectories. Digital-symbol
  likelihoods and decoder feedback are not available for analog video. A
  single Tikhonov component reduces to a single-loop-like approximation.
  Ω uses a numerical circular density rather than copying its digital decoder.
- [Bahl, Cocke, Jelinek and Raviv, posterior decoding](https://research.ibm.com/publications/optimal-decoding-of-linear-codes-for-minimizing-symbol-error-rate).
  Forward HMM probabilities are causal. BCJR backward messages require future
  observations; full block smoothing is not a deployable causal claim here.
  Viterbi preserves only a winning path and may commit prematurely at a fade.
  Traceback adds delay and memory unavailable in this schedule.
- [Hamilton, Fard and Pineau, compressed predictive states](https://jmlr.org/papers/v15/hamilton14a.html)
  and [Subramanian et al., approximate information states](https://jmlr.org/papers/v23/20-1165.html).
  These motivate task-relevant state compression. Their general approximation
  results do not prove that our chosen 14 moments are sufficient statistics,
  or that Lloyd/DAgger iteration is globally optimal.

ML finite-window frequency estimation improves noise averaging but attenuates
video changes over the window; long audio/GNSS integration is unsuitable for
multi-MHz video. GNSS carrier estimators additionally rely on known spreading
codes/pilots absent here. EKF/UKF assume a locally unimodal posterior and can
lose alternative phase trajectories. IMM combines process hypotheses, but
explicit mode probabilities consume state precision when discretized. Particle
filters approximate the same causal posterior, with sampling variance and
resampling collapse; larger offline filters remain future comparisons, not
claimed implemented improvements. Heavy-tailed observations reduce outlier
weight but cannot know whether a large change is valid detail from magnitude
alone. The chosen contamination model and wide transition prior test both
possibilities probabilistically.

## Hardware-constrained student

The teacher exports 14 causal posterior features: circular phase moments,
phase-frequency cross moments, inferred noise-mode probabilities, four
frequency-mass bins, posterior frequency mean/spread and low-frequency mass.
Clustering learns free-form state prototypes. States are not a prescribed
phase×frequency×confidence grid. The closest diffuse prototype is state zero,
matching the actual startup feedback. No full PAL/NTSC timing counter exists.

Initialization clusters conditional posterior features for each retained raw
10-bit address into T observation tokens. Joint refinement alternates:

1. Unroll the current transducer on the training IQ, with zero startup per
   independent stream and persistent feedback within each stream.
2. Aggregate teacher posterior features and frequency targets conditional
   on `(actual_student_state, observation_token)`.
3. Project the conditional posterior onto the nearest learned state; fit its
   DAC action from the same conditional frequency targets.
4. Reassign each raw address to the token minimizing posterior projection
   error plus a bounded DAC reconstruction term across recurrent occupancy.
5. Unroll again, exposing state-distribution drift to the next iteration.

Unvisited transition words retain their previous policy. This is a practical
closed-loop distillation/coordinate optimization, not a guarantee of a good
local minimum or lossless information bottleneck. The recurrent table and
encoder both change, rather than teaching targets to a separate static DAC.
All final words are emitted through the real shared-LUT compiler. Tests check
all three fields of every word and actual stream output across multiple 32K
wrap lengths. Interpreter and address-trace implementations are independent.

The same table handles all trained noise regimes. Uncertainty is implicit in
which posterior prototype is visited. There is no external C/N value, per-line
label, fake raster, LUT glide or EDGE/PAIR switch inside the student. Real
sync can persist as a frequency-history belief when observations are partially
corrupt; the representation cannot regenerate indefinitely absent timing.

## Reproducibility and selection discipline

Install `numpy`, `scipy`, `numba`, `scikit-learn` in an isolated environment.
Exact run versions/seeds and results are preserved in `docs/data/omega_optimized`.
From the repository root:

```sh
export PYTHONPATH=v4/tools:v4/tools/dsp_search
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
export C5VRX4_SEARCH_LANE=ultrafine
python v4/tools/dsp_search/omega_research.py --output /tmp/omega-new
python v4/tools/dsp_search/omega_sync_risk.py --teacher-config v4/docs/data/omega_optimized/teacher_jump0.06.json --output /tmp/omega-risk-new
python v4/tools/dsp_search/omega_direct40.py --teacher-config v4/docs/data/omega_optimized/teacher_jump0.06.json --output /tmp/omega-direct-new
python v4/tools/dsp_search/omega_confirm.py --model v4/tools/omega_model.json --output /tmp/omega-confirm.json
python -m unittest test_omega
python v4/tools/verify.py
```

Training uses seed 610190, held-out seeds 710190/810190, confirmation seed 910190.
Teacher jump weights and all nine allocation/layout candidates are selected
on training **only**, with a predeclared scalar. No holdout re-tuning. Per-case
raw hashes establish exact stimulus pairing. Completely independent confirmation
expands deviation, amplitude, DC, I/Q imbalance and phase error, covers full
interlaced fields and physically constructs waveform disturbances before
quantization. Channel echoes use the existing coherent delayed-signal model.
Gain changes scale both signal and noise before quantization. Phase jumps alter
the received waveform; interferers add another complex carrier.

Metrics retain SINAD, high-pass detail correlation, missed H/V, false sync,
sync amplitude/width error, large-error rate/magnitude and fade recovery.
Short ladder windows contain active video and H sync but not every full
vertical train, so full-field confirmation is essential. Recovery compares
with the same demod on a matched no-outage stream, using a clean-counterpart
lag/gain frozen for both noisy streams. The initial recovery-only noisy-lag
inconsistency was corrected by `omega_recovery_audit.py`, with exact IQ-hash
checks; SINAD/sync/detail scores were already frozen and are unchanged.
Missing recovery is `null`,
not a fabricated number or a pass. No real board capture was available in
this workspace; replay input is explicit and absence is recorded.

## Firmware integration contract

Ω is an extra explicit LAB entry. Existing modes, defaults and NVS selections
keep their values. Its state/phase metadata says zero phase/frequency grid
states: those labels are inapplicable to a free-form belief codebook.
The diagnostic identifies the posterior FSM and experimental acceptance.
`Y`/SETUP DEMOD selects LAB entries; `R` returns to the established rollback.

The stored AutoFit deviation/centre may remap **DAC6 only** at deliberate
program load. Ω has its own pristine cache and fit kind, full stopped-engine
word comparison against generated goldens and write/restore mapping probe.
Token/state fields survive calibration. The inference policy itself is fixed;
there is no claim that output remapping corrects every transition-prior mismatch
for another transmitter. A fresh fit is stored for the next deliberate load,
not an in-flight calibration reload. Existing Fusion/CVT is gated on EDGE mode
and cannot swap or glide Ω's program. Reference-demod guards keep CPU flywheel,
line repair, idle raster and live level/DC table writers disabled.

## Physical test protocol

1. Preserve a known-good firmware and NVS backup. Bench-test with goggles,
   not in flight. Select the opt-in Ω entry, verify its model ID, state/token
   count, stopped-engine self-test, raw32K geometry and no program reloads.
2. Start with a strong VTX and real moving scene/OSD. Compare Ω, EDGE+AF,
   PAIR+AF and Fusion at the same RF gain/lane/BW/output load. Record colour,
   detail, latency, DAC swing, sync-tip/porch voltage and pulse widths at 75 Ω.
   Check goggles that previously rejected small CVBS (including HDZero AV-in).
3. On a conducted, adequately attenuated setup or repeatable shielded geometry,
   increase RF attenuation in small recorded steps. Capture goggles video,
   contiguous IQ and status at each step. Avoid confusing attenuator dB with
   synthetic C/N or a range multiplier. Count true/missed/false H/V and clicks.
4. Exercise fast attenuation fades (microseconds to milliseconds), obstruction,
   movement/multipath, gain transitions and other VTX deviations. Record whether
   real moving image survives and returns, rather than a cosmetically stable
   stale image. Acquire fresh DCO-on strong/weak IQ for host paired replay.
5. Log TX-empty/FIFO/underrun and wrap counters over long runs, measure acquired
   sample cadence and output duplicates, and verify no C/N-triggered program
   swap. Source equivalence and successful compilation do not establish these.
6. Roll back immediately on malformed sync, goggle rejection, persistent loops,
   overload or loss of moving video. Do not promote this experiment before
   physical acceptance of both weak sync and strong detail.

## Results and remaining investigations

The numerical results and build evidence are appended after the frozen run.
A failure to cross EDGE/PAIR's frontier is a result, not an instruction to
retune on confirmation cases. Further work should first identify whether
teacher approximation, posterior-feature compression, observation-token loss,
closed-loop state drift or output reconstruction causes the loss. This study
cannot prove a universal noise/detail limit for every legal C5 schedule.

### Alternative schedule under test

`omega_direct40.py` implements eight repeated single bundles, each reading and
writing one byte. Its LUT16 address holds four raw I2/Q2 bits and six recurrent
state bits; the lookup supplies DAC6 and next-state6 directly. The encoder
lookup is gone. This gives 40 million distinct output decisions/s with 64
free-form states and 16 raw observation combinations, while retaining the same
shared 2-KiB RAM. Three different retained bit pairs per component are tested.
Both A and B update the history, but each loses six of its original eight IQ
bits. It is an explicit source-level alternative, not an imaginary extra RAM
or parallel lookup. Its teacher targets are generated at every 25 ns, so the
sample-A target cannot see sample B. Prefix/stride tests verify causality.
Source equivalence tests use a 70,000-byte continuous stream and count exactly
one instruction bundle per emitted byte. Assembly feasibility does not prove
C5 clock/FIFO timing. It is not promoted into the firmware unless independent
quality gates pass; negative results and generated source remain available.

### Task-weighted sync-risk refinement

The first free-form students reduced posterior-feature distortion but compressed
real sync plateaus toward the dominant active-video distribution. This was
visible on **training** as shallow sync and missed pulses even at high C/N;
cleaning up the score alignment did not remove it. `omega_sync_risk.py` tests
an additional predeclared grid using fresh held-out seeds 1110190/1210190:
weights 2, 8, 32; 128×8/64×16; layouts 4411/3322. Training sample weights are
`1 + weight * clip((-teacher_hz - 0.7MHz)/1.2MHz,0,1)^2`. They emphasize causal
teacher evidence of low-frequency plateaus in prototype clustering, observation
assignment, state projection and DAC fitting together. No true raster label,
future IQ or C/N value is passed to the transducer. This is an asymmetric task
risk, not ordinary unbiased frequency MSE; it can bias ambiguous black/chroma
into false sync. Its merit is determined on held-out and independent confirmation
cases. Final selection compares training utility across both grids, never
chooses a winner on confirmation scores.

### Frozen held-out results and acceptance verdict

Final training winner: **128 states × 8 observations, PAIR4411 retained bits,
low-frequency risk weight 2, model 249d670670f5**. Selection pooled all 21 shared-LUT
candidates using training utility alone. The extra three direct40 models were
rejected as a family before promotion. The winning teacher has jump weight 0.06;
0.18 and 0.35 progressively worsen training utility (7.588,3.638,-0.195).
All results below are synthetic, not RF sensitivity, range or goggle acceptance.

On the final candidate's fresh held-out seeds 1110190/1210190 (four matched
PAL/NTSC cases per C/N), using clean-counterpart frozen latency:

| C/N | EDGE misses / SINAD | PAIR misses / SINAD | Ω misses / SINAD | Ω false sync per line |
|---:|---:|---:|---:|---:|
| 2 | 3 / 6.45 | 29 / 2.43 | 26 / 4.26 | 0.34 |
| 4 | 0 / 6.04 | 11 / 4.97 | 19 / 5.81 | 0.57 |
| 6 | 0 / 8.31 | 8 / 5.60 | 2 / 8.86 | 0.61 |
| 8 | 0 / 5.17 | 4 / 7.35 | 1 / 8.84 | 0.52 |
| 10 | 0 / 8.25 | 1 / 10.63 | 4 / 8.28 | 0.50 |
| 13 | 0 / 8.74 | 0 / 11.35 | 0 / 10.60 | 0.32 |
| 16 | 0 / 6.53 | 0 / 13.46 | 7 / 8.66 | 0.68 |
| 20 | 0 / 6.23 | 0 / 11.23 | 0 / 10.21 | 0.25 |
| 30 | 0 / 7.15 | 0 / 13.20 | 0 / 9.39 | 0.39 |

**The acceptance gates fail.** At 2/4/6 dB Ω misses 47 pulses vs EDGE's 3, and
its strong-signal false sync/detail remain worse than PAIR. At 6/8 dB it can
retain more SINAD than either control on these cases, but that does not outweigh
the sync failures or establish flight usefulness. It is preserved as an explicit
opt-in bench experiment with the vetoes in `range_options.json`, not a promoted
receiver algorithm. A codebook-learning result that reduces feature distortion
is not necessarily a CVBS-quality improvement.

The original unweighted 64-state/16-token student's separate matched holdout
(seeds 710190/810190) lets us diagnose teacher→student loss on the SAME IQ:

| C/N | Bayesian teacher SINAD | unweighted student SINAD | teacher misses | student misses | EDGE misses |
|---:|---:|---:|---:|---:|---:|
| 2 | 6.12 | 2.89 | 13 | 39 | 5 |
| 4 | 8.52 | 6.02 | 5 | 18 | 1 |
| 6 | 9.88 | 7.31 | 5 | 9 | 0 |
| 13 | 13.82 | 10.53 | 0 | 0 | 0 |
| 20 | 16.05 | 11.15 | 0 | 0 | 0 |
| 30 | 16.62 | 10.44 | 0 | 0 | 0 |

The teacher has recoverable-detail headroom in the synthetic IQ, but also loses
more weak sync than EDGE. The student adds observation compression, finite-state
projection and recurrent distribution drift. These ablations do **not** identify
one universal bottleneck or prove that every hardware FSM must share this
frontier. The current 14-moment representation is not a sufficient statistic for
an arbitrary multimodal posterior. The two-sample 4411 encoder discards six bits
of sample B and then compresses 1024 raw addresses to eight tokens; history cannot
recover arbitrary discarded information. DAC6 adds quantization but does not
explain all the observed loss. No independent second LUT or extra full-rate CPU
DSP path exists in this implementation.

Direct40 independently misses every tested H-sync pulse even at 30 dB
(22 across two short cases), while PAIR/EDGE miss 0. It reaches only 4.74 dB
SINAD vs PAIR 16.92 on that strong screen. Trading observation precision for
twice as many posterior updates is therefore not a superior schedule here.
Its generated source passed the actual C5 assembler and 70k-sample dataflow
proof; it remains negative research, not a firmware option.

Next investigations should target full-posterior/multimodal predictive features,
state-metric selection that preserves rare plateaus without biasing chroma,
closed-loop policy optimization with genuine detail/sync risk, and a calibrated
colored-noise/folded-ADC likelihood. Require a demonstrably stronger causal
teacher on fresh DCO-on board IQ before another large search. Neither thousands
of additional constants nor a claim of mathematical global optimality is
justified by this experiment.

### Independent confirmation (seed 910190)

Thirty full-field scenarios and 36 separate disturbance scenarios are retained
in `docs/data/omega_confirmation.json`, each with matched IQ hashes and complete
metrics. Full fields cover PAL/NTSC at 3/6/20 dB, five content types including
moving C5 OSD glyphs. The confirmation envelope widens deviation to 0.55–1.6,
RMS to 2.2–4.8 cells, DC to ±0.3 RMS, gain imbalance to ±10% and I/Q phase error
to ±5°. The larger fixed-ultrafine amplitudes intentionally include folding;
these are demod robustness tests, not a simulation of the complete gain-control
supervisor. The teacher likelihood is not calibrated to every such fault.

| scenario family | cases per model | EDGE missed H+V | PAIR missed H+V | Ω missed H+V |
|---|---:|---:|---:|---:|
| full fields |30|211|5213|6465|
| disturbances |36|145|922|683|

These aggregate counts mix reception levels and transmitter/board faults;
per-level/per-case metrics are in the JSON. They reaffirm the veto rather than
establish a performance win. Interference, gain jumps and fades can produce
false sync; the final Ω model has more false pulses than EDGE in both families.
Real board IQ and goggle timing remain unavailable/unverified. Error-sample
rates and p99 magnitudes are surrogate click measurements; they are not a
measured classification of every RF phase slip or physical FM-click event.


## Validation evidence

`docs/data/omega_validation/` records the audited PR head, pinned model hash,
host logs and build evidence. `tools/verify.py` passes 23 C regressions,
exhaustive unwrap and source-driven DSP tests, including native LAB value 13.
Five Ω tests cover folded quantized likelihood, causal prefix/stride behavior,
all nine shared-LUT allocations/layouts, continuous 70k-byte source equivalence,
direct40 cadence and calibration preservation of token/state fields.
The actual ESP-IDF v6.0.2 C5 assembler accepts the selected Ω program and
the negative direct40 schedule. The packed-word message from the assembler
is not permission to allocate a second physical LUT.

Host source equivalence and compilation establish executable dataflow, not
measured 40-MHz FIFO deadlines or physical CVBS voltage. Board acceptance
remains explicitly pending. No synthetic dB result is claimed as a range gain.

ESP-IDF **v6.0.2** with GCC 15.2.0 compiled and linked successfully for
ESP32-C5. The application image is **1,304,288 bytes**; its 3-MiB partition
has 59% free. Artifact SHA-256 values, compiled-source hashes and full log
are recorded in `docs/data/omega_validation/validation.json` and
`firmware-build.txt`. The manager was disabled locally because there are no
managed manifests and sandbox process ancestry breaks its `psutil` lookup.
Identical compiler helpers were moved outside workspace syncing to retain
execute permission; wrappers affect cross compilers only. No repository
build-setting workaround was introduced. The concurrently added research
script/document do not change any recorded compiled-source hash.
