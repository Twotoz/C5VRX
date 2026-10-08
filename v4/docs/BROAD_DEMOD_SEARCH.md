# Broad offline demod search

Extends C5VRX by Twotoz and the contributors and the existing BitScrambler
research. Source: https://github.com/Twotoz/C5VRX; website and Discord invite:
https://twotoz.github.io/C5VRX/. Preserves Louis Hitchcock's recovery lineage.

## Search space

`tools/detector_study/broad_search.py` does not initialize from VLP56, use its
encoder or use its DAC transfer as a training constraint. VLP56 and OVP56 are
reference detectors only. It searches 62 supported LUT8 address layouts:

- 16/24/32/40/48/56 current IQ tokens;
- previous token compressed by one, two or three bits;
- no middle context, middle Q sign, middle I sign, both signs, or raw middle
  bits 2 and 6 (these latter bits are not amplitude estimates);
- phase-only, phase/radius, rotated Cartesian clusters and arbitrary cluster
  partitions. Random initialization also changes retained previous-state groups.

There are two actual schedule families: endpoint mapping and endpoint mapping
with wired middle-sample context. The 62 layouts and thousands of quantizers
are not thousands of fundamentally different architectures. Counter-arithmetic,
recursive output-state, different schedules and span75 models remain outside
this new search space; they must not be declared exhausted by this experiment.

Each candidate gets an independently fitted integer DAC table. For a fixed
encoder and these training counts, each occupied bin's clipped, rounded
conditional mean minimizes empirical squared endpoint-frequency error over all
64 DAC codes. This is an exact conditional integer result, not a global
encoder/architecture optimum, a filtered-video optimum or RF sensitivity proof.
No encoder coordinate-descent refinement or filtered-table refinement is
performed in this first broad screening pass.

## Hardware and evaluation gates

All candidates have one 2048-byte LUT8 (256 raw decoder entries plus at most
1792 map/padding entries), eight instruction slots, two lookups and two bundles
per 50 ns, raw Q4/I4 RX40 and duplicated DAC6 [D,D] TX40. No live sample CPU,
transformed ring, concurrent RX/TX BitScrambler or boundary resets. Saving bits
from the intervening raw byte requires no third lookup. Compiler tests verify
all 62 layouts against the source-driven BitScrambler model, including middle
context's previous-bundle alignment. This is not hardware timing acceptance.

Training seeds are 1301/1302. A fast empirical endpoint-loss screen keeps two
candidates per initialization family and middle-bit choice, so the cheap proxy
cannot eliminate an entire non-phase or middle-context family. Selection uses
1401/1402 and three scenarios (random, bars, multitone; gain/DC/IQ skew stress).
The weak score is mean SINAD minus 0.002 times >40-IRE errors per 1000 samples
at input C/N 0/2/4/6 dB. Each strong scenario's loss beyond 0.8 dB relative to
HC50 receives a penalty; this is a ranking penalty, not a hard eligibility guard.

Four finalists are frozen before final seeds 1501/1502/1503. All comparisons
use identical input streams and the same truth-video score. LUT detectors use
the same passive resistor-DAC conductance model before the goggle filter.
Adjacent40 is the ideal every-sample reference. Unwrap75 uses the generated
static STD150 LUT, original trajectory routing and [D,D,D] cadence. Its port
is regression-checked against the historical repository model.

These scores therefore differ slightly from older linear-DAC-code tables.
Do not subtract results from the old study to derive a gain. The generator's
resistor model is still an assumption about analog hardware, not a measurement.
The signals have periodic horizontal sync but are not full PAL/NTSC frames;
there is no fading/multipath simulation or physical RF-range acceptance here.
Global gain/offset fitting cannot establish correct absolute CVBS scaling.

## Reproduction

Offline NumPy/SciPy only; firmware and CI generation stay standard-library based:

```sh
OPENBLAS_NUM_THREADS=1 python v4/tools/detector_study/test_broad_search.py
OPENBLAS_NUM_THREADS=1 python v4/tools/detector_study/broad_search.py \
  --output /tmp/c5vrx-broad-search --variants 2048 --shortlist 4
```

The run writes all unique manifests, selection scores, frozen finalists,
compiled programs, per-scenario final scores and a summary. Duplicate manifests
are counted separately from attempted configurations. The shortlist argument
controls final evaluation count up to eight; it does not change the 40-model
family-quota screening cap. No results cause automatic firmware promotion.
A candidate needs clean absolute-level validation, further signal stress tests
and board acceptance before becoming a selectable/default demod.

## First 2048-configuration run

2,048 attempts produced 1,997 distinct trained encoder/table/layout manifests.
Forty family-preserving candidates received filtered-video selection evaluation;
four frozen finalists received independent final evaluation. The selected new
candidate is `broad-01453-polar` (56 current tokens, 28 retained previous classes,
no middle context). It does not beat the existing references overall and is not
promoted. Non-phase/arbitrary clusters and middle contexts were evaluated, not
silently excluded because phase/polar candidates won the first screening.

Mean output SINAD in dB, three final seeds and all three scenarios:

| Input C/N | Unwrap75 | HC50 | VLP56 | OVP56 | New polar finalist |
| --- | --- | --- | --- | --- | --- |
| 0 | 1.017 | 0.577 | 0.932 | 1.081 | 1.029 |
| 2 | 2.024 | 1.374 | 2.011 | 2.198 | 1.965 |
| 4 | 3.683 | 3.046 | 3.853 | 3.971 | 3.386 |
| 6 | 5.812 | 5.731 | 6.226 | 6.142 | 5.099 |
| 8 | 7.964 | 8.391 | 8.443 | 8.274 | 6.706 |
| 14 | 13.060 | 13.930 | 13.431 | 13.199 | 10.305 |

The new candidates' strong-signal loss exposes the limitations of an endpoint
squared-error training objective; thousands of starts alone do not fix that.
A next round should refine encoders and tables against filtered-video/absolute
CVBS criteria, and search additional counter/state schedules. New objectives
must use new selection/final seeds rather than repeatedly tuning these finals.

OVP56 exceeds Unwrap75 here by 0.064/0.174/0.288/0.330/0.310/0.139 dB at
0/2/4/6/8/14 dB input C/N. Its >40-IRE errors are lower at 0 through 8 dB,
but it has a small nonzero error tail at 14 dB where Unwrap75 has none.
These are modest model-dependent output differences, not an RF dB/range claim.
The experiment does not isolate Louis's physical gain-recovery benefit.

Full seed/scenario evidence: `broad_search_final.json`; aggregate and manifest
hash: `broad_search_summary.json`; frozen finalist manifests:
`broad_search_frozen.json`. The deterministic reproduction writes all 1,997
manifests and their compiled finalist sources; the hash records that full set.
