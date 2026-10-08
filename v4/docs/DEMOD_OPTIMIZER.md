# Hardware-constrained demod search and OVP56

Extends C5VRX by Twotoz and contributors, its Phase8/HC50/VLP56 research and
source-driven BitScrambler models. Canonical source: https://github.com/Twotoz/C5VRX;
website and Discord invite: https://twotoz.github.io/C5VRX/. Louis Hitchcock's
staged recovery and corrected #184 evidence remain separate from these synthetic
results. No new model has board or measured RF-range acceptance.

## Result and scope

OVP56 is the best eligible model found for the declared weak-signal objective in
this search. It is not a mathematical proof of the best possible C5 receiver.
It retains the pinned VLP56 encoder; the winning change is its optimized DAC
pair table. Free joint encoders, a midpoint-quadrant architecture and a recursive
state4 tracker were also tested. Their failures are recorded rather than hidden.

New/invalid `c5vrx4/ref_demod` selects OVP56 (4). Existing 0/1/2/3 retain
HC50/HR50/Golden50/VLP56. SETUP DEMOD or serial `g` cycles
HC50 -> HR50 -> Golden50 -> VLP56 -> OVP56 -> HC50, saves and reboots.
One `g` from OVP56 returns to HC50. Select VLP56 for the previous weak-signal
comparison. Staged Direct Gain recovery applies to every selection.

All active models keep Q4/I4 RX40, raw32K, TX-only BitScrambler, two lookups and
two bundles per 50 ns, unique CVBS20, duplicated DAC6 at physical TX40, eight
instruction slots and a 2048-byte LUT8. No CPU handles live samples. Existing
span75 observers, repair and live LUT writers remain gated.

The subsequent [broad search](BROAD_DEMOD_SEARCH.md) removes the VLP starting
point and transfer constraint, varies encoder/middle-context address layouts,
and includes Unwrap75 in a matched-stream comparison. Its separate DAC-voltage
metric and seeds must not be mixed with the numerical tables below.

## Search and numerical guarantees

1. Joint pair search: 32/40/48/56 current tokens; previous token or token>>1;
   plain and low-radius initial encoders; weak and balanced training mixes.
   Counts/target sums are accumulated over raw endpoint pairs. For a fixed
   encoder, the conditional mean is the exact empirical squared-error optimum
   before DAC rounding. Sequential encoder reassignments count both endpoints
   and the self-intersection; each fixed-table step and refit cannot increase
   this training loss. The encoder search is local, not exhaustive.
2. Video fit: a linear operator includes duplicated DAC samples and the finite
   causal five-MHz goggle filter. Its adjoint is tested. LSMR solves a regularized
   floating-table problem. The final solver also uses L-BFGS-B to enforce the
   actual 0..63 DAC bounds; simply clipping the unconstrained fit failed the
   level guards. Integer rounding is followed by independent evaluation; it is
   not claimed to be the exact integer quadratic optimum.
3. Midpoint family: preserve two raw sign bits from the intervening captured
   sample in CounterB, with previous-phase7/current56 and 28x56 mapping. All
   three captured points supply information without a third lookup. Loss of
   previous-endpoint precision outweighed the extra context in this experiment.
4. Tracking family: previous-phase7/current56 plus two output-state bits in the
   LUT word. The controller carries those bits around the raw decoder lookup.
   Training starts with clean state only for initialization, then uses actual
   generated feedback. Selection/final runs never receive clean state. The
   closed-loop tracker performed poorly and was rejected.
5. Final transfer-constrained fit: 768 clean radius/CFO/video-level conditions
   constrain absolute mean DAC levels against VLP56. Radii: 1.25/1.5/1.75/2/2.25/2.5/3/3.5/4/4.5/5/5.5 ADC cells;
   CFO: .25/.5/.75/1/1.25/1.5/1.75/2 MHz; video: -40/-20/0/20/40/60/80/100 IRE. Eligible integer models must stay
   within one DAC code at every level and within 0.8 dB strong-signal SINAD per
   test scenario. Winner: level penalty 100, regularization .1. Maximum clean
   mean-level error is 0.6715 DAC code; bounded solver converged, with no
   out-of-range entries. This is a source/model voltage guard, not a scope test.

The unconstrained video winner reduced clean sync-to-white DAC span to about
79-88% of VLP56. It was rejected despite better fitted weak-signal SINAD.
The bounded winner preserves the absolute level constraint instead of relying
on the scorer's fitted gain/offset to conceal that shrinkage.

Training and selection use disjoint seeds per stage; see optimizer_search.json.
Pair training: 401-403. Midpoint: 411-413. Filtered video: 421/422. Tracking:
431-433. Final dense bounded selection: 591/592; frozen final: 791-793. A further
untouched confirmation uses 911-913, after the winning table is frozen. Those
confirmation seeds are not used to refit, rank or change the winning table.
Data from negative earlier stages informed the next architecture/objective;
there is no claim that earlier final sets remained untouched across that work.

## Independent confirmation

The table below uses only seeds 911-913, the original 1-MHz CFO / 6.7-MHz
sync-to-white deviation random-detail scenario and the established scorer.
Each cell is output SINAD dB / errors above 40 IRE per 1000 samples.

| Input C/N | VLP56 | OVP56 |
| --- | --- | --- |
| 0 dB | 1.156 / 174.761 | 1.332 / 165.969 |
| 2 dB | 2.417 / 113.444 | 2.624 / 106.950 |
| 4 dB | 4.264 / 51.156 | 4.432 / 53.447 |
| 6 dB | 6.673 / 14.968 | 6.642 / 17.272 |
| 8 dB | 8.983 / 2.453 | 8.843 / 3.194 |
| 14 dB | 14.238 / 0 | 13.968 / 0 |

Across random detail, bars and multitone stress scenarios, OVP56 gains
0.147/0.190/0.124 dB SINAD at 0/2/4 dB C/N, and loses
0.050/0.160/0.235 dB at 6/8/14. At 2 dB C/N aggregate large errors fall from
132.99 to 126.15 per 1000 (about 5.1%). At 4, 6 and 8 dB it has more large
errors. This is a modest edge-focused tradeoff, not a huge additional gain over
VLP56. An untouched clean-level check (radii 2.2/4.1, CFO .7/1.3 MHz,
-40/-10/30/100 IRE) stays within 0.797 DAC code; sync-to-white span stays
at 98.3-101.7% of VLP56. The earlier sparse 36-level constrained fit failed this
check (up to 5.12 DAC code error) and was superseded by the dense fit.


Full band scores and per-scenario data are in optimizer_final.json.

The frozen 791-793 run also compares HC50 and every-sample adjacent40; its
aggregated results are in optimizer_search.json. All models use the same raw
samples and truth/scoring within a scenario. Equal score/filter bandwidth is
not a full PAL/NTSC, chroma, sync-lock or RF-attenuation test.

## Reproduce

Run from repository root; offline research needs NumPy, SciPy and a C compiler
(for the tracking rollout). The firmware generator and native regression need
only the standard Python library. Research commands never overwrite the pinned
firmware codebook:

```
OPENBLAS_NUM_THREADS=1 python v4/tools/detector_study/optimize_pair.py --output /tmp/pair
OPENBLAS_NUM_THREADS=1 python v4/tools/detector_study/optimize_video.py --pair-output /tmp/pair --output /tmp/video
OPENBLAS_NUM_THREADS=1 python v4/tools/detector_study/optimize_tracking.py --video-output /tmp/video --output /tmp/tracking
OPENBLAS_NUM_THREADS=1 python v4/tools/detector_study/optimize_levels.py --output /tmp/levels
OPENBLAS_NUM_THREADS=1 python v4/tools/detector_study/test_optimizer.py
python v4/tools/generate_ovp56.py
python v4/tools/verify.py
```

compile_pair_demod.py validates and emits source for all three implemented
hardware families. Its output must pass the source model and actual assembler;
a schema check is not a complete hardware timing proof. Native OVP56 regression
checks every 65536 raw endpoint pair, arbitrary startup counters, duplicate DAC
bytes, LUT budget, two-bundle cadence and routing. CI never runs the optimizer;
it assembles the pinned JSON deterministically.

## Remaining boundaries

The simulated channel is the existing 10-MHz analog model with AWGN. Stress
cases vary carrier offset, deviation, raw scaling, DC and I/Q skew. Bars and
multitone include high-frequency detail but are not full PAL/NTSC colour video.
Multipath, real PHY folding/AGC transients, alternative analog filters, live
LUT8 addressing/throughput, DAC resistor loading and measured RF sensitivity
remain unvalidated. The captured 40-MS/s path still does not recover every
80-MS/s modem source sample. This search does not remove that source limitation
or enumerate every conceivable legal demod architecture.
