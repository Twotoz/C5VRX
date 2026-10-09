# An amplitude-aware causal model, and the unresolved hardware compression

This continues the operator's instruction to build a good model, starting at
PR190 head `25dfb78e805ec1b78298de1c3eb9c346f0d1c463`. Real board IQ is useful
validation, but is not a prerequisite for investigating a better model.

**A substantially better weak-sync offline reference is implemented. A
qualified all-range C5 implementation is still not obtained.** The reference
infers amplitude/noise from IQ, keeps continuous phase/frequency hypotheses,
and consumes both full raw IQ bytes per50ns. The tested legal FSM compressions
lose much of that benefit. Their failure is evidence against these compression
methods, not a proof that a good hardware model cannot exist.

No firmware selection or default changes in this update. Ω remains quarantined.
All new LUTs/source are preserved as unqualified research controls, not menu
options. The PR must remain unmerged.

## Model

The state of particle j is `(phase, frequency, frequency-slope, A/sigma class,
motion class, weight)`. There are2048 particles,36 persistent amplitude/noise
classes, and two optional motion classes. No true C/N, amplitude, video truth,
line clock or VTX deviation enters `adaptive_particle.filter(raw,cfg)`.

Amplitude hypotheses are1,2,3,4.8,7,10 lane cells; independent component-noise
sigma hypotheses are.04,.2,.6,1.5,3,6 cells. This removes the old fixed-total-RMS3
assumption. Classes migrate with probability.001 per raw observation, from a
uniform prior. Initial phase is uniform over2π; frequency is uniform over
-7..9MHz. The fixed prior is not selected using the test's C/N. Frequency is
bounded at±10MHz, an explicit model assumption.

For each component code c, let D(c) be its complete preimage under the actual
sign-preserving lane extraction and simulated Q10 clipping. D(c) is a union of
ADC-cell intervals, not a single four-bit signed cell at high amplitude. The
component likelihood is

```
P(c | mu,sigma) = sum_[lo,hi] in D(c) [Phi((hi-mu)/sigma)-Phi((lo-mu)/sigma)]
L(q | phase,A,sigma) = P(Icode | A*cos(phase),sigma)
                      * P(Qcode | A*sin(phase),sigma)
```

The code reuses the checked folded-cell integral in `omega_bayes.py`. Its
probability normalization and agreement with independently sampled quantized
Gaussian data are tested. Phase is continuous; only the likelihood evaluation
uses256 phase points and linear interpolation. It is not a32-phase trajectory
grid.

Observations use `((1-.005)*L + .005/256)^.65`. The contamination term supports
outliers. **Power-tempering is generalized Bayes, not the exact posterior of
an independent-Gaussian observation model.** It is an explicit robustness
assumption for model mismatch/correlated RX-filter noise. The actual undocumented
analog ADC likelihood, DC errors and I/Q imbalance remain unverified.

Frequency is in MHz. In trajectory mode:

```
slope' = .94*slope + Normal(0,.08)
f' = f+slope'
phase' = phase+2*pi*f'/40
```

With probability.06 a wider innovation `Normal(0,1.8)` is applied and slope is
reset. This admits legitimate rapid modulation, not just noise rejected by a
phase-difference threshold. In plateau mode slope decays by.1 and frequency
innovation sigma is.04. The optional latent motion chain is

```
                 next plateau   next trajectory
plateau              .994              .006
trajectory           .020              .980
```

The posterior, not true C/N, decides which histories survive. There is no sync
schedule, PAL/NTSC line timer, periodic program swap or injected raster. A flat
video/sync frequency can accumulate evidence; an incompatible fast trajectory
can take over. This is a multiple-hypothesis nonlinear estimator, not a PLL.

Weights are updated by the current IQ byte and normalized. Systematic
resampling occurs only when effective particle count drops below N/2. The
frequency estimate is the weighted posterior mean every second raw sample;
nearest DAC6 code is produced under squared voltage risk and a fixed affine
calibration. `decode` applies the same **synthetic AutoFit surrogate**
(deviation/centre supplied by the test) as the controls. Thus IQ inference is
blind to those parameters, but these CVBS scores do not demonstrate blind
on-board calibration of this reference. Scoring truth is never an inference
input.

This is a Monte Carlo approximation with process/likelihood mismatch. It is
not a globally optimal model or a rigorous recovery upper bound. It cannot run
at20/40MS/s on the C5 CPU; it is an offline teacher.

## Independent reference results

Three process alternatives were ranked using eight training cases at seed2210190
(C/N4/20, RMS3/7, PAL/NTSC). The walk variant wins that three-model screen.
The motion-mixture variant was separately tested and frozen before its own
confirmation. They are not switched by a test-C/N oracle or blended into a
claimed deployment result.

The walk reference's independent seed2310190 has24 cases: C/N2,6,13,30,
RMS1.5,3,7, and two content/standard/deviation cases per condition. Six cases
per C/N, mean SINAD/detail and total H+V misses:

| C/N dB | EDGE SINAD / misses / detail | PAIR | walk reference |
|---:|---:|---:|---:|
| 2 | 2.86 / 8 / .385 | 1.04 / 31 / .549 | 3.44 / 0 / .555 |
| 6 | 3.85 / 0 / .505 | 5.27 / 11 / .753 | 5.34 / 0 / .686 |
| 13 | 3.94 / 0 / .519 | 8.52 / 0 / .887 | 8.17 / 0 / .852 |
| 30 | 3.70 / 0 / .518 | 10.21 / 0 / .942 | 11.11 / 0 / .950 |

The walk's weak large-error samples (>40IRE) average175.89/142.61 per1000,
vsEDGE191.65/170.17 at2/6dB. False-sync scores are.197/.045 vsEDGE.424/.136.
These are synthetic error metrics, not measured physical FM-click or RF gains.
At13dB it still loses some detail to PAIR: no full-range domination is claimed.

The motion reference's independently frozen seed2410190 has32 cases, adding
texture and OSD to zoneplate/checker, two deviations(.69/1), and RMS3/7. Eight
cases per C/N:

| C/N dB | EDGE SINAD / misses / detail | PAIR | motion reference |
|---:|---:|---:|---:|
| 2 | 3.99 / 3 / .386 | .60 / 51 / .453 | 4.90 / 0 / .523 |
| 6 | 5.07 / 0 / .495 | 4.49 / 8 / .658 | 6.12 / 0 / .642 |
| 13 | 5.21 / 0 / .512 | 8.19 / 0 / .838 | 8.84 / 0 / .836 |
| 30 | 4.68 / 0 / .526 | 11.19 / 0 / .950 | 9.51 / 0 / .958 |

It has zero false sync in these tests, but its weak >40IRE error counts
148.55/145.86 exceed EDGE125.80/106.27. At30dB SINAD is lower than PAIR despite
slightly higher detail. This is a real Pareto trade-off. Do not select different
references per C/N and then call the combined rows one adaptive model.

A separate frozen *default trajectory* stress test (not the selected walk)
uses seed2710190, RMS10, C/N4/20, PAL/NTSC. At20dB it scores8.84dB and zero
misses, versusPAIR3.79dB/3misses andEDGE3.42dB/9misses. At4dB it still misses2
vsEDGE0. Correct folded-cell likelihood plus history can recover information
that the simple signed-cell decoder loses; this is not proof of board folding
or restored physical range. A CW test also infers a3-to10-cell gain change
without receiving the amplitude and recovers the correct1MHz frequency.

These measurements use the earlier corrected latency protocol: one lag fitted
on clean IQ and frozen for noisy IQ, fixed voltage gain, no noisy per-case
alignment optimization. Raw hashes prove each model in a comparison receives
identical IQ. Different seed sets are not pooled as if they were paired.
All windows are32768 raw samples (0.8192ms): they test local video/H-sync,
not full-field V-sync, millisecond fades or actual goggle acceptance.

## Hardware compression

The reference sees both full raw bytes:16 observation bits per span. The legal
PAIR-style encoder first retains ten bits via4411 or3322. Thus the teacher/student
gap includes **initial observation loss as well as state/policy compression**.
It must not all be attributed to state count.

The student uses the existing real shared-word LUT16 compiler. All1024 words
simultaneously contain DAC6, next-state bits and encoder token bits, totalling
six plus `(10-b)` plus b =16 bits. No independent second LUT is used. RX40,
raw32K, eight instruction slots, two bundles/lookups per50ns and duplicate DAC6
TX40 are unchanged. It is executable dataflow, unlike the previous research
counter schedules requiring TX20; it is nevertheless **unqualified picture
quality** and not selectable firmware.

Tested allocations:

| States | Observations | Token bits | Status |
|---:|---:|---:|---|
| 256 | 4 | 2 | posterior and predictive students fail |
| 128 | 8 | 3 | posterior and predictive students fail |
| 64 | 16 | 4 | best tested student, still fails |
| 32 | 32 | 5 | observation-rich student fails |
| 16 | 64 | 6 | observation-rich student fails |

All use both4411 and3322 legal input layouts. Encoder assignments, state
transitions and DAC words are updated jointly in closed-loop distillation.
Posterior features include amplitude/noise, phase/frequency coupling, slope and
frequency uncertainty. The old14-feature default is preserved byte-identically
and regression-tested; optional geometry enables this new teacher.

The predictive variant clusters causal future-phase characteristic moments:
`E[exp(j*(phase+2*pi*(h*f+.5*h*(h+1)*slope)/40))]` for h1,2,4 raw intervals,
along with present phase and frequency moments. This is a prediction using the
current posterior, **not actual future IQ**. It makes uncertainty implicit in
moment length and avoids allocating explicit confidence bits. It improves the
best tested compression slightly, but does not solve it.

At seed2610190, the64-state/16-token predictive4411 student misses49 pulses at2dB
and21 at6dB, vsEDGE4/0. At30dB it scores5.57dB/detail.674 vsPAIR11.19/.949 and
misses3 vs0. Its32-state/32-token competitor nearly suppresses all genuine sync
(81/80 misses at2/6dB). Fewer false pulses are not a sync-recovery improvement.

### Direct sequence-policy learning: also a negative result

`fsm_sequence_train.py` optimizes a hard finite-state rollout with truncated
backpropagation through an explicitly **straight-through surrogate**. Forward
runtime picks exactly one token and one state; there is no hidden posterior
vector required by hardware. Softmax derivatives exist only during offline
training. The encoder, complete transition choices and DAC values are trained
together against causal reference code targets, with a lower-code risk weight.
The entire IQ sequence affects offline parameter training; runtime and teacher
outputs remain causal. This is nonconvex optimization, not the exact gradient
of an integer policy or an optimality proof.

Three epochs worsen teacher-code loss from.012590 to.018285,.016274,.015007.
**The selected checkpoint is epoch0, identical to the initial LUT.** Metadata
records this explicitly; no successful refinement is claimed. A fresh seed2810190
confirmation of that retained control misses59/23 pulses at2/6dB vsEDGE4/0,
and gives5.59dB at30dB vsPAIR11.22. The local surrogate adjoint is checked by
finite differences, not assumed correct because a training loss exists.

The study comprises four reference variants,16 distillation configurations
and one sequence-refinement attempt, with124 distinct independent stimulus
scenarios (seed231:24,241:32,261:32 shared across three compression families,
271:4,281:32). There are752 paired model rows. Training seeds221/251 are separate;
sequence optimizer seed291 controls optimization, not confirmation selection.
Every negative result is saved.

## What is demonstrated and what is not

- A causal gain/noise-aware model can preserve real weak sync and additional
  video information from these same quantized IQ traces.
- Fixed RMS3 and eight-token Ω are not a sufficient foundation for that task.
- Raw-observation reduction, finite state capacity and the *tested* state
  discovery/policy learners lose the reference's benefit.
- A global hardware impossibility, measured RF gain, real moving goggle video,
  arbitrary VTX generalization and an all-range C5 replacement are not proved.

This is evidence to develop a better compact predictive policy or a legal
information-retaining schedule, rather than demanding board captures before
attempting a good model. Do not keep retuning this failed clustering/straight-
through method indefinitely. Better observation-guided particle proposals and
colored-noise likelihoods can strengthen the teacher; policy learning should
explicitly measure observation loss separately from memory/transition loss.
Purely causal decoding cannot regenerate indefinitely absent raster timing.

## Reproduction and checks

Use Python3.12 and `tools/dsp_search/omega_requirements.txt`. From the repository:

```sh
export PYTHONPATH=v4/tools:v4/tools/dsp_search
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 C5VRX4_SEARCH_LANE=ultrafine
python v4/tools/dsp_search/adaptive_particle_search.py --output /tmp/particle
python v4/tools/dsp_search/adaptive_particle_confirm.py --output /tmp/motion
python v4/tools/dsp_search/adaptive_particle_stress.py --output /tmp/folded.json
python v4/tools/dsp_search/adaptive_particle_student.py --output /tmp/posterior
python v4/tools/dsp_search/adaptive_particle_student.py --output /tmp/predictive --features predictive
python v4/tools/dsp_search/adaptive_particle_student.py --output /tmp/observation --features predictive --bits 5 6
python v4/tools/dsp_search/adaptive_sequence_search.py --model /tmp/predictive/selected_model.json --output /tmp/sequence
python v4/tools/dsp_search/test_adaptive_particle.py
python v4/tools/dsp_search/test_fsm_sequence.py
python v4/tools/verify.py
```

Fresh output paths are required. `--confirm-only` resumes a frozen student
selection after a stopped validation; it cannot select or retune from heldout
results. The initial source check mistakenly compared unused byte bits6/7
against a DAC-only simulator; it was corrected to compare all physical DAC6
bits. No scores from that halted run were used to change selection.

Validation: five reference tests, three sequence/compatibility tests,23 C
regressions and source DSP checks pass. Selected policies pass70k-byte physical
DAC6 source/model equivalence. All20 generated student programs (including
selected copies) compile with the actual Espressif ESP32-C5 assembler from
ESP-IDF6.0.2. Source/binary/tool hashes and logs are in
`data/adaptive_particle/validation`. Program cadence is the existing validated
TX40 schedule. No firmware runtime changed, so the previous quarantine firmware
build evidence remains applicable; this update does not claim a new IDF build
or board run.

Mathematical precedents, not automatic wideband-FPV solutions:

- Collings/Moore1995, [adaptive HMM FM demodulation](https://doi.org/10.1016/0165-1684(95)00100-X):
  couples continuous channel estimates and discrete information states.
- Wadhwa/Madhow2013, [quantization-aware Bayesian synchronization](https://wcsl.ece.ucsb.edu/sites/default/files/publications/synchronization_allerton13_aseem.pdf):
  models coarse observation likelihood, but its QPSK acquisition assumptions
  do not justify holding analog-video phase/frequency constant over symbols.
- Li et al.2007, [sequential MCMC instantaneous-frequency estimation](https://doi.org/10.1109/ICASSP.2007.367052):
  emphasizes nonlinear trajectories and particle impoverishment; its windowed
  polynomial estimator is not adopted as a deployable causal C5 decoder.
- Tam/Tam/Moore1973, [fixed-lag FM smoothing](https://doi.org/10.1016/0005-1098(73)90032-0):
  motivates a separately labelled delayed upper-bound study, not future-IQ
  leakage into these causal tests.

For hardware validation preserve NVS/PHY, compare current PAIR/EDGE with the
same moving camera and stepped attenuation, and record actual DAC voltage,
H/V timing and gain/DC diagnostics. These failed student LUTs are not recommended
for goggles. No newly demonstrated hardware mode is available yet.
