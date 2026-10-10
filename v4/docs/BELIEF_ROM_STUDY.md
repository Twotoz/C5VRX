# Bayesian belief ROMs and causal input-history schedule

## Outcome and acceptance status

No candidate in this study meets the combined EDGE weak-sync and PAIR
strong-detail gates. No candidate is promoted to a firmware menu or default.
OMEGA value13 remains quarantined following the operator's reported ugly
high-amplitude picture. The repository contains executable experimental
BitScrambler sources, not a claimed working replacement. The mission to find
a better continuous C5 demodulator remains open. These experiments neither
certify a global optimum nor prove hardware impossibility.

The new work tests posterior-state projection, predictive-state projection,
closed-loop joint transition/encoder/DAC refinement, inferred observation
quality, and a genuinely different legal input-history schedule. The
state-conditioned encoder is a Bayesian control of the previously negative
architecture, not a new invention. No external C/N switch, CPU sample-rate
DSP, LUT reload controller, or raster flywheel is introduced.

## Mathematical model and research basis

Let q be the folded 4-bit IQ cell and x=(phase,frequency,amplitude,noise).
The observation likelihood integrates the complex Gaussian density over
all analog cells mapped to q by the C5 lane/quantization model. A narrow
phase-bin point likelihood alone would not account for amplitude clipping
or lane folding. The grid reference uses 64 phase bins, 33 frequency bins
from -5 to 7 MHz, and 12 amplitude/noise regimes. PAIR4411's 10-bit
observation contains full A IQ and only the B I/Q signs. Its joint likelihood
integrates the discarded B bits and models the intervening 25 ns phase step.

The generalized robust likelihood is

\[
\widetilde L(q\mid x)=\big[(1-0.005)L(q\mid x)+0.005/|\mathcal Q|\big]^{0.65}.
\]

Tempering makes this a generalized Bayesian estimator, not exact Gaussian
maximum likelihood. The prediction uses a reflected five-tap frequency
walk, 0.025 jump mixture, fractional circular phase interpolation across
50 ns, and 0.002 regime migration. The reference is causal and never receives
true C/N. The prior and dynamics are research choices, not universal optimal
video statistics. The grid's frequency extent is also a limitation.

For each ROM state s store a belief b_s. For a token t:

\[
p_{s,t}(x)\propto L(t\mid x)\,\mathsf T b_s(x),\qquad
s'=\arg\min_j d(p_{s,t},b_j),\qquad
c=\operatorname{clip}_{0:63}\operatorname{round}(\mathbb E[f\mid s,t]\,K+B).
\]

The distance is either Hellinger distance on latent beliefs or a Hellinger
projection of predicted next-IQ and frequency distributions. The encoder
clusters conditional latent beliefs for the 1024 raw addresses. This
constructs all entries, including states/tokens not visited in training.
The output is conditional-mean frequency rounded to DAC6; it is not a
certified optimum for the nonquadratic CVBS loss. Later refinements weight
sync-frequency histories but do not implement a hidden raster timer.

The continuous particle teacher from `ADAPTIVE_PARTICLE_STUDY.md` provides a
second reference. It sees both full raw IQ bytes, maintains continuous phase
and frequency with 36 amplitude/noise hypotheses, and uses the walk model
(2048 particles, innovation .25, jump .08). Snapshots compress its marginal
phase/frequency belief to 64x65 bins. They discard amplitude/noise/frequency
slope correlations: this is **not** distillation of its complete posterior.
A new feature estimates E[A²/(A²+2 sigma²)] from the inferred hypotheses.
It is predictive observation quality, not supplied true C/N. The legacy
18-feature API and original decoder outputs are preserved byte for byte.

Relevant primary research:

* [Observations preprocessing and quantization for nonlinear filters](https://epubs.siam.org/doi/10.1137/S0363012997331147)
  motivates quantized likelihood tables, not a video quality guarantee.
* [Optimal projection filters with information geometry (2023)](https://link.springer.com/article/10.1007/s41884-023-00108-x)
  distinguishes local projection criteria from optimal filter trajectories.
  Our discrete codebooks are heuristics, not applications of a continuous
  projection-filter optimality theorem.
* The HMM FM, quantization-aware estimation, nonlinear particle FM and
  fixed-lag sources evaluated in the preceding two study documents remain
  relevant. Future-IQ smoothing is not used in any deployed/source model here.

## Joint closed-loop optimization

`refine_belief_rom.py` rolls out the actual integer FSM, collects causal
teacher posteriors under the states that the student actually visits,
re-estimates state prototypes, and updates transitions, DAC codes and
observation assignment together. It evaluates five refinement rounds plus
the unchanged initialization. True video ranks only training policies.
Each frozen selected policy then receives a separately seeded confirmation.
No confirmation result changes its table.

Empty-state reseeding inserts distant teacher posteriors while retaining
startup state0. Its projection distortion can decrease without improving
CVBS. Adaptive geometry uses circular phase moments and uncertainty-weighted
frequency distributions. Quality-aware variants additionally use the inferred
observation-quality feature. These are offline compression experiments;
they are neither extra runtime confidence registers nor a new PLL gain.
The matched-quality reseeding variant preserves each inserted belief's
corresponding quality rather than assigning an arbitrary quality .5.

A real startup bug was found before initial confirmation: a single diffuse
state among sharp steady-state prototypes created an absorbing state0.
Those sources are retained in `*_absorbing` with abort notes. The corrected
code reserves acquisition posteriors and tests initial token escape. Results
below concern corrected sources. Removing that bug did not make them pass.

## Exact resource and schedule audit

A single physical 1024x16-bit ROM stores six DAC bits, (10-b) next-state
bits and b encoder bits in each word. Thus 256x4, 128x8, 64x16, 32x32 and
16x64 are legal allocations. There is no independent encoder ROM. Generic
allocations were evaluated in the preceding study; this round explores
128x8/64x16/32x32 and context 256x4/128x8/64x16.

The actual compiler emits eight bundles, two lookups per 50 ns, duplicate
DAC6 output at TX40, unique samples at20M, raw RX40 and the existing32K ring.
State-context addressing uses two previous-state bits instead of B signs.
It therefore discards the B observation and is explicitly measured as such.

The new `history_observation` schedule uses the C5's existing 64-bit input
shift register. With **prefetch false**, it starts at zero. A read shifts
old input toward the low bits and inserts new bytes at the high end.
Controller input bits36..39/32..35 hold full current A IQ; bits23/19 hold
I/Q signs from the previous A, 50 ns older. The register's low16 MEM1 bits
can coexist with LUT16. MEM1 high16 overlaps the LUT region and is not used.
This preserves real past information without another ROM or a future sample.
It sacrifices the current B signs and adds a fixed four-raw-sample (100 ns)
latency compared with the ordinary schedule. Two zero-input warmup spans
are included in training and the exact model. That added latency is declared
before scoring; noisy results cannot choose a new lag.

`bs_model.py` previously ignored prefetch=false initialization. It now models
zero initialization and the 64-bit shift correctly; the original prefetch=true
path is unchanged. An independent explicit shift-register test covers mixed
reads and both startup configurations. Compiler/source simulation and official
assembler acceptance do not establish FIFO timing, RF reception or actual
picture quality. The physical input semantics were checked against ESP-IDF
v6.0.2 `bitscrambler.rst`, its assembler and C5 target JSON.

## Benchmark protocol and limitations

Training: eight PAL zoneplate / NTSC checker cases, C/N4 and20 dB, amplitude3
and7, 16384 raw samples each. Confirmation:24 cases, C/N2/6/13/30 dB,
amplitude1.5/3/7, both standards,32768 raw samples each. Frozen clean-derived
latency uses the corrected PR scorer. AutoFit parameters here are a common
synthetic calibration surrogate, not measured board AutoFit; runtime true
C/N is never supplied. Every comparator uses the exact same hashed IQ.

`detail` is the existing high-frequency recovery score, not subjective
picture quality. Misses are pulse events summed across the six cases at
one C/N. SINAD/detail are case averages. See JSON for false-sync, click and
latency-corrected metrics. This round's short records do **not** certify
V-sync over complete frames, millisecond fades, multipath, varying deviations,
DC imbalance, gain transitions or a physical range improvement. Basic gates
already fail, so extending these losers into a much larger stress search
would not establish the requested solution. Prior broader controls and
Fusion comparisons remain in the preceding studies; Fusion is not silently
relabelled as a continuous oracle controller here.


| Frozen family | Seed | Misses2dB | Misses6dB | SINAD30dB | Detail30dB | Misses30dB |
|---|---:|---:|---:|---:|---:|---:|
| belief_adaptive_policy_rom | 4410190 | 66 | 65 | 4.51 | 0.735 | 61 |
| belief_context_policy_rom | 3410190 | 66 | 66 | 3.17 | 0.668 | 66 |
| belief_context_rom | 3010190 | 66 | 66 | 2.74 | 0.726 | 66 |
| belief_endpoint_policy_rom | 4610190 | 66 | 66 | 3.92 | 0.677 | 66 |
| belief_history_policy_rom | 4810190 | 65 | 64 | 4.80 | 0.748 | 65 |
| belief_particle_policy_rom | 4010190 | 45 | 42 | 1.19 | 0.552 | 60 |
| belief_particle_split_rom | 4210190 | 66 | 66 | 3.51 | 0.700 | 66 |
| belief_policy_rom | 3410190 | 66 | 66 | 3.96 | 0.645 | 66 |
| belief_predictive_rom | 3010190 | 66 | 66 | 2.96 | 0.605 | 66 |
| belief_quality_policy_rom | 5010190 | 56 | 52 | 3.89 | 0.719 | 52 |
| belief_quality_split_rom | 5210190 | 66 | 62 | 3.26 | 0.626 | 66 |
| belief_rom | 3010190 | 66 | 66 | 5.58 | 0.797 | 66 |
| belief_sync_policy_rom | 3710190 | 60 | 54 | 1.77 | 0.598 | 46 |

For a paired control example on history seed4810190: EDGE has4/1 misses at
2/6dB, while the history ROM has65/64; at30dB PAIR scores10.23dB SINAD,
0.943 detail and0 misses versus history4.80,0.748 and65. These are synthetic
short-record results, not measured receiver dB/range gains.

On the paired ablation (seed3110190, eight cases), the full-address grid
reference scores4.33 dB at2dB C/N with zero misses, versus EDGE3.10/zero;
at30dB it scores9.67 versus PAIR11.05. Compressing observations to eight
tokens drops strong SINAD to6.34 but retains zero strong misses. Projecting
that same token reference into128 ROM states drops it to3.99 and16 misses.
This identifies both observation and recurrent-state compression losses;
it does not show that every128-state transducer must fail. The best retained
particle split policy selects epoch0 unchanged, another negative optimization
result rather than an improvement. The input-history schedule is legal but
its selected decoder misses almost every sync and fails strong detail.

## Reproduction

Install `tools/dsp_search/omega_requirements.txt` in a fresh virtual environment.
Run from the repository root:

```sh
export PYTHONPATH=v4/tools:v4/tools/dsp_search
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 C5VRX4_SEARCH_LANE=ultrafine
python v4/tools/dsp_search/search_belief_rom.py --output /tmp/belief-latent --bits 3 4 5 --layouts 4411
python v4/tools/dsp_search/search_belief_rom.py --output /tmp/belief-predictive --bits 3 4 5 --layouts 4411 --projection predictive
python v4/tools/dsp_search/search_belief_context_rom.py --output /tmp/belief-context
python v4/tools/dsp_search/belief_rom_ablation.py --model v4/docs/data/belief_rom/b3_4411.json --output /tmp/belief-ablation.json
python v4/tools/dsp_search/test_belief_rom.py
python v4/tools/dsp_search/validate_belief_rom.py --idf /path/to/esp-idf --output /tmp/belief-validation.json
```

`reproduce_belief_policy.py --print-only` prints the exact commands for all
closed-loop families and their seed/flag combinations. Omit that flag to
execute them, supplying a fresh output parent. Archived JSON contains all
selected and rejected LUTs, scores, and training configurations. The frozen
confirmation can be rerun independently using `confirm_belief_rom.py ROOT`;
it atomically replaces only the selected-model confirmation JSON, never the
policy. The validation manifest hashes every archived program, official
assembler output and confirmation file. Actual tools/SDK versions and logs
are archived under `docs/data/belief_validation`.

## Verified contributions and next discriminating investigations

Verified: real shared-ROM sources, a causal input-history schedule, corrected
prefetch=false modeling, closed-loop joint policy refinement, causal
particle belief snapshots, negative independently seeded benchmarks and
regression/build evidence. **No new across-range demodulation improvement is
verified.** No board capture or physical receiver was available this round.

The critical next investigation is an encoder/state design that retains
phase-hypothesis correlations without destroying the phase precision needed
for chroma. The marginal particle distillation loses inferred-regime and
slope correlations; adaptive geometry did not repair this. A different
legal state/observation representation or a trained transducer that truly
optimizes closed-loop CVBS risk remains possible. Local belief projection
and these finite search runs are not certificates of global optimality.
Do not repeat the failed families as new wins, extend their search indefinitely,
or deploy the history schedule with an arbitrary PLL table expecting it to
fix the decoder. Missing RF cannot provide indefinitely absent raster timing;
no causal FSM without a timing reference can regenerate that information.

## Hardware test gate for any future qualifying candidate

Before adding a selectable LAB mode, validate its transport clock and startup
on a C5 board. Keep RANGE32, EDGE+AF and PAIR+AF as controls. Use actual goggles
with a moving checkerboard/zoneplate, fine OSD and saturated color transitions;
record clean high-gain and clipping/amplitude behavior first. Test multiple
PAL/NTSC camera/VTX deviations, then step a calibrated RF attenuator through
strong/transition/weak reception and brief fades, holding all other settings
constant. Capture raw IQ and DAC/CVBS on the same run where possible. Measure
real sync-tip/blanking voltages and H/V pulse durations on a scope, picture
motion/detail, false/missed sync and recovery time. Watch DMA underruns and
BitScrambler continuity rather than assuming a source-equivalence test proves
real-time execution. A video that looks clean but freezes or suppresses valid
chroma does not pass. Promote no default or RF-range claim until those paired
hardware observations support it. The current failed tables are research
artifacts and should not be flashed as a claimed solution.

## Final validation evidence

152 archived sources accepted by official ESP32-C5 assembler; 13 selected tables each passed 70000-raw-sample DAC6 source equivalence. 248 distinct independent IQ records, 1296 comparator rows (shared seeds are counted once). Twelve new invariant tests and23 existing C regressions passed. Full1180-step and final reconfigure/build with official ESP-IDF6.0.2 passed; app image1304288 bytes. The build retains the existing firmware demodulators; failed new sources are not linked as selectable modes. See hashed manifest and archived logs.
