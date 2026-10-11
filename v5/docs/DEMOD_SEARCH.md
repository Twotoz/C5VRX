# Offline demod architecture search

This is research for **C5VRX by Twotoz and the C5VRX contributors**. It extends
the analytic PAIR shared-LUT generator from PR #190 and the confidence-aware
prediction investigated in PR #55. The simulator and frozen U85G experiment
from this stacked PR are the starting point. This is an automated way to
generate and reject demod candidates, not a claim that a better receiver or
the globally optimal demodulator has been found.

`v5/research/demod_search.py` synthesizes a complete resident LUT and a
BitScrambler program for each design. All sample processing remains offline
in this study. The firmware keeps its existing default demodulator.

## Direct LUT evolution: removing the analytic-family restriction

The formula sweep is retained as a baseline, but **`lut_evolve.py` optimizes
the actual LUT words**. An observation need not be an angular sector. A
retained state need not represent phase or frequency. Every observation
token, next-state assignment and six-bit DAC code can change independently.
All 16,384 bits of the shared LUT are mutable. Sparse DAC moves preserve
useful candidates; unrestricted word/field redraws permit arbitrary tables.

The ten input address bits are also genes: any ten distinct bits from the
sixteen raw pair bits may be selected, covering all **8,008 input subsets**.
The earlier four MSB layouts are seeds rather than restrictions. Exact
state/token relabeling preserves behavior while allowing non-angular state
encodings. Random LUTs provide additional starting points. Width niches keep
4/5/6-token-bit architectures in the population; mutations, elite selection
and random restarts run for multiple generations, with resumable checkpoints.

This searches the complete word representation **within the existing
two-lookup program skeleton**, not every program executable by the C5.
The retained-state carry is still O26..O31, observations use ten selected
raw bits, and token/state widths remain 4/6, 5/5 and 6/4. Conditional
instruction schedules, wider state-carry schemes and different transports
are outside this engine. These are explicit scope boundaries, not a claim
that those alternatives are physically impossible or that U85Q is optimal.

```sh
python -m unittest discover -s v5/test -p 'test_lut_evolve.py'
python v5/research/lut_evolve.py v5/research/data/evolve_wide_20261011 \
  --population 16 --generations 8 --seed 240011 --jobs 4 --finalists 3
```

The default budget is 32 population members and 32 offspring generations.
Increasing it is supported; it does not guarantee a better receiver. Training
uses six 220,000-sample cases; shortlisted genomes then face all nine longer
screening cases before freezing. The final 56 two-field cases use **new seeds**
(511xxx/512xxx etc. training, 521xxx/522xxx/523xxx validation), distinct from
the previous formula study. No validation metric guides evolution.

`checkpoint.json` includes the full genomes, metrics, RNG state and source
hashes at each completed generation. `--resume` continues an interrupted run
with the same population, seed, sources and baseline. Resume is refused after
finalists have been frozen, to avoid tuning against already viewed final
results. A new study after inspecting the final rows needs a new holdout.

All observations/outputs/states share the one 1024-word table. Program
generation adjusts only the legal field wiring and raw input-bit selection,
with the same eight-block read/write cadence. Tests cover arbitrary tables,
behavior-preserving state/token permutations, all mutable fields, input
subset mutation, hash corruption and fresh holdout seeds. No realtime CPU
model or automatic firmware promotion is added.

The first evolutionary run is archived in `data/evolve_wide_20261011/`:
**147 genomes over eight offspring generations**, 882 short training
comparisons, 54 longer screening comparisons and 168 independent full-field
comparisons. Fifteen host tests pass (the original ten plus five direct-LUT
tests). A separate set of 57 mutated/random-state programs, including altered
input selections, assembled successfully for ESP32-C5. Physical timing is
still untested.

The leading genomes improve weak-case mean luma SINAD by about **0.32 dB in
the final matrix**, with only about 0.02 dB mean strong-case loss. This avoids
the earlier predictive family's large strong-image penalty, but the candidates
still lose up to about **66 percentage points of displayed lines** in individual
weak/echo cases. They fail the longer screen and independent sync gates. No
candidate is exported or enabled. Short-crop training did not reveal those
sync failures; that is a limitation of this first evolutionary run, not a
reason to loosen the acceptance gate. More generations and longer/broader
training are needed to search further; these results do not establish a
hardware or information limit.

## Search space and hardware gate

Every proposed design uses raw packed Q4/I4 pairs at IQ40, a single TX
BitScrambler, one shared **1024x16 LUT (2 KiB)**, two lookups per 50 ns,
unique CVBS20 duplicated as `[D,D]` at TX40, and the existing passive DAC6.
No RX BitScrambler, CPU sample processing, transformed ring, extra lookup,
sync regeneration or reset at a DMA boundary is introduced.

The high bits of each shared word encode a stage-1 observation token. The
remaining bits encode stage-2 DAC output and next retained state. Both address
spaces span the same 1024 words; separate tables are not assumed.

| Token bits | State bits | Architectures explored |
| --- | --- | --- |
| 4 | 6 | Phase6 tracker; Phase4 + frequency2; Phase3 + frequency3 |
| 5 | 5 | Original Phase5 tracker; Phase4 + frequency1; Phase3 + frequency2 |
| 6 | 4 | Phase4 tracker with more observation tokens |

Seven retained state bits are rejected: this skeleton carries state in
O26..O31. The worker transfers the token and retained state into a ten-bit
address; the controller retains the next state while forming the next pair
address. Eight alternating controller/worker blocks preserve the instruction
count and read/write cadence. Different bit wiring still needs physical timing
and continuity testing even after the ESP32-C5 assembler accepts it.

The generator varies pair layouts `4411`, `3322`, `3331`, `3313`, near-origin
observation tokens, bounded sample-B direction assistance, loop gain,
confidence-dependent origin gain and output law. Predictive designs divide
the retained bits between circular phase and a coarse frequency estimate.
They predict phase one 50-ns step ahead, correct its innovation, and either
emit the phase advance or updated frequency. Frequency history is quantized;
the emitted frequency update is not restricted to those coarse history bins.

This is deliberately a broader architecture search than the U85G formula
sweep. It cannot recover information already lost at the ADC tap. It also
does not exhaust all possible nonlinear state encodings, learned tables,
conditional instructions or larger hardware topologies.

## Selection protocol

1. Include exact U85Q and U85G controls, then generate a deterministic,
   seeded mixed-family population. Parameters are explored offline. The
   budget is explicit; no unexplained millions-of-models claim is made.
2. Evaluate nine screening cases with **actual bits 9,6,5,4 folding**,
   including wide deviations 1.6/1.7/2.0, low C/N, PAL/NTSC, the known U85G
   echo failure, strong video, envelope stress and a fade. Default screening
   crops contain 300,000 raw IQ bytes, enough for the goggle model's burn-in.
3. Compare every case directly with exact U85Q on the same received bytes.
   Use the worse of the simulator's ideal and peak-clamp goggle lock,
   together with luma SINAD. A candidate fails if a case loses >3 percentage
   points of displayed lines when U85Q displays at least 50%, crosses below
   90% lock where U85Q maintains it, or loses >0.5 dB luma SINAD. Require at
   least +0.2 dB mean weak-case SINAD improvement. These are chosen engineering
   tolerances, not proofs of visually identical pictures.
4. Rank by gate eligibility first, fewer violations second, then a score
   combining mean weak gain, worst SINAD delta and worst displayed-line delta.
   Thus a good average cannot hide a sync or strong-image failure.
5. Freeze the best three non-control designs **before** creating held-out
   cases. If screening rejects every design, test the least-failing ones for
   diagnosis; they remain ineligible for export even if final tests pass.
6. Validate on **56 independent two-field cases** with PAL/NTSC, C/N
   2/4/6/25 dB, deviations 1.6/1.7/2.0, three echo configurations,
   deeper/longer fades, gain radii 2.5/5.5 and residual offsets +/-0.5 MHz.
   No retuning on these rows. Export `candidate.bsasm` only if both gates pass.
   A synthetic pass remains hardware-pending.

Screen and validation raster/noise seeds are disjoint. The final matrix
includes new echo/fade parameters but also intentionally repeats the known
failure channel with different seeds. It is not a blind test of all possible
propagation. Repeated development against final results would consume this
holdout: choose new seeds/channels for the next experiment.

## Reproduce and increase the budget

Use Python 3.12 and the pinned dependencies in `v5/research/requirements.txt`:

```sh
python -m unittest discover -s v5/test -p 'test_*demod*.py'
python v5/research/demod_search.py v5/research/data/search_wide_20261011 \
  --budget 96 --seed 210011 --jobs 4 --finalists 3
```

The output directory must not exist: experiments cannot silently overwrite
older negative findings. Use a new name for another run. Increase `--budget`
for more exploration; `--jobs` only changes worker concurrency. `--screen-only`
is useful for a quick diagnostic but never exports a winner.

The atomic JSON checkpoints preserve the protocol, source and baseline-LUT
hashes, every design and per-case metric, frozen candidates, final rows and
decision. All failures remain in the archive. Screening designs and finalists
can also be synthesized with `program(Design(**record['design']))` for an
independent assembler check; rejected designs are research artifacts only.

## First run: 96 designs, no promotion

`data/search_wide_20261011/` records the complete run above. All 96 parameter
sets produced distinct LUTs. Each was accepted by the ESP-IDF 6.0.2 assembler
with the ESP32-C5 capability file. Ten host tests pass, including exact U85Q
control reproduction, independent integer decoding, storage/schedule checks,
seed separation and rejection of hidden sync regressions. This validates the
search machinery and assembly syntax; physical timing remains untested.

There were **864 screening comparisons** and **168 held-out comparisons**.
Zero designs passed screening. The three least-failing non-control designs
were frozen and tested anyway, for diagnosis. All three failed final gates:

| Frozen design | Phase / frequency bits | Mean weak SINAD delta | Worst SINAD delta | Worst displayed-line delta |
| --- | --- | ---: | ---: | ---: |
| `b464ab01d2cb` | 4 / 2, phase-advance output | -0.008 dB | -4.661 dB | -3.751 percentage points |
| `f8322271a391` | 3 / 2, phase-advance output | +0.063 dB | -7.515 dB | -1.852 percentage points |
| `e7809c1eb3e5` | 3 / 2, frequency-update output | +0.180 dB | -9.000 dB | -30.939 percentage points |

The coarse phase/frequency tradeoff can occasionally improve noisy luma but
costs too much clean-picture precision in this tested family. This run does
not rule out a better state encoding or demodulator. `decision.json` records
`no_candidate_passes`, and **no candidate program was exported or enabled**.
An automated search is now available; a better field receiver is not yet
demonstrated. Real IQ and physical A/B measurements remain the next evidence
needed to validate the simulator and any future synthetic winner.

## Remaining evidence limits

- The study targets the fixed **WIDE 16 MHz receive profile**. Passing here
  says nothing about the narrow/default profiles or every VTX/camera.
- The actual lane bit extraction is modeled, but its ADC scaling/headroom,
  channel filter, RF noise, gain control and DAC transfer are simplified.
- The propagation model normalizes average received signal power. Echo/fade
  results compare demodulation at equal average C/N, not absolute path loss.
- Calibration uses noise-free synthetic video; real goggles, AFC, DC errors,
  chroma quality and board timing can fail in ways this luma/sync test misses.
- Random bounded parameter exploration is not a proof of optimality. More
  candidates do not fix a wrong simulator or inadequate acceptance gates.
- Before firmware promotion: physical continuous-stream timing/soak test,
  clean video and chroma check, held-out measured IQ captures, controlled RF
  attenuation A/B, then the same outdoor route and antenna geometry. Record
  whether any improvement is in luma, sync, measured attenuation or distance.

See [PAIR_DEMOD.md](PAIR_DEMOD.md) for the previous negative result and
[FIELD_TEST.md](FIELD_TEST.md) for board-side diagnostics.
