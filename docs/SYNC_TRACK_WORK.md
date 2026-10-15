# Exact TRACK work reduction and a rejected slicing prototype

This adapts C5VRX by Twotoz and the C5VRX contributors, from the
[canonical source](https://github.com/Twotoz/C5VRX). The
[official website](https://twotoz.github.io/C5VRX/) carries the Discord invite.
All changes here are temporary research core variants; this study does not
enable a firmware writer or prove an80us C5 service deadline.

## Work that the outer budget does not bound

The pinned core's outer `s_evals < budget_evals` check does not interrupt:

| Helper | Work inside one call |
| --- | --- |
| `pulse_width` | Up to434 `lowm` calls for a1300-sample broad pulse |
| `find_pulse` | Up to65 search points at unlocked window96, plus candidate widths and edge refinement |
| `v_slot` | Broad presence test, broad edge/width search,32-point steady-tip test and falling-edge refinement |
| `learn_levels` |176 additional adjacent-step evaluations |
| `find_relock` | Roughly150 stride16 probes per original gap, plus edge refinement and up to87 width probes per candidate |

Each original `lowm` reads ten Phase8 values and unwraps nine adjacent steps.
The six seven-field detector scenes include clean PAL/NTSC, a0.6-field weak
fade, and source disappearance followed by a0.37-line phase jump. The worst
turn reaches730 evaluations/7300 phase reads, beyond the nominal192 budget.
The scenes use the existing C generator and contain picture, burst, H and
equalizing/broad V intervals. Input hashes/configuration are in the artifacts.

## Exact rolling average

The width/search walker usually advances by three samples. Its nine-step
averaging window overlaps the previous one by six steps. Keep those nine
signed steps, their sum and the final phase. At `k=previous_k+3`, remove the
three oldest steps and append three newly unwrapped steps. This needs three
new Phase8 reads instead of ten, while retaining **every** original averaged
sample, noise-hole check, width, edge and source predicate.

Reset before every `sfw_run`, before any `synth` that might write source bytes,
on discontinuity and on source/ring/phase/context replacement. Counter rollover
is an explicit invalidation; an implementation should require increasing
absolute positions as well as a difference of three. Never infer original
source from this cache. Existing full-footprint provenance gates still apply.
An `int8_t steps[9]`, `uint8_t phase/index`, `int16_t sum` and64-bit previous
position fit about24bytes including alignment. Steps are exactly[-128,127];
the nine-step sum fits signed16bits. Wrap the index with a conditional branch
rather than introducing a general modulo operation in the hot path.

| Scene | Original worst phase reads/turn | Rolling |
| --- | ---: | ---: |
| PAL clean |4584|1973|
| NTSC clean |4714|2124|
| PAL fade |7230|5753|
| NTSC fade |7300|5900|
| PAL changed-phase return |6940|5701|
| NTSC changed-phase return |6880|5627|

All six per-turn complete-FSM trace hashes match exactly, including evaluation
counters and H/V acquisition/lock/relock state. All input raw bytes remain
unchanged. Evaluation counts intentionally stay unchanged: the same decisions
are computed more cheaply. The remaining worst fade work includes disjoint
stride16 relock probes, which do not benefit from this overlapping-window cache.

`research/sync_rolling_verify.py` additionally passes1000000 direct actual
`lowm`/reference comparisons with full phase/threshold range,874811 contiguous
and125189 discontinuous calls, ring and phase mutations with explicit resets,
and positions near64-bit rollover. It executes the unchanged full keeper
scene/output tests at kp85 and kp50, with callback commits: both DAC parities,
ACTIVE IQ/DAC preservation, clean/fade/weak RF, source loss/phase return,
PAL/NTSC bootstrap transitions and long-loss counter boundaries all pass.
This full keeper run uses the research coarse-acquisition candidate too; the
rolling comparison itself holds that acquisition path identical in both arms.

These are exactness and work results, not cycle measurements. C5 timing must
measure the final compact helper in IRAM with the actual RAM phase table.
The current worst5900 phase reads and other control work still require a
physical service measurement before anyone claims80us acceptance.

## Slicing that must not ship

The separate `--slices` prototype caps new raw Phase8 work at1920 reads/turn by
checkpointing private detector state and replaying an exact primitive
transcript until an interrupted iteration completes. It preserves existing
qualifiers, adds no synthetic evidence and writes no raw IQ. Nevertheless,
its extra V-interval latency makes TRACK hit its existing half-ring stale
limit: six resets in each clean PAL/NTSC case, and four or five during
fade/return. The first clean PAL stale coordinate is827908, versus no stale
resets in the baseline. It also needs a6144-byte transcript, full FSM snapshot
and jump state. End-of-run lock alone conceals that failure; the artifact keeps
the repeated acquisitions and missing V fields explicit. It is a rejected
prototype, not a production coroutine or evidence of bounded live service.

## Reproduction

The exact UTF-8 core snapshot SHA256 is
`acdf35c16ce0688eb735878a5ad8c74fafb24f8d3c218a2006fb8eb88ea18d2a`,
preserved in `research/data/sync_track_core_acdf35c1.c.gz`. Scripts default to
that source; `--current` explicitly selects a new production source and the
new report records its hash. Temporary variant hashes are also recorded.
Current public headers/test fixtures must remain compatible with the snapshot;
source-anchor changes stop variant generation instead of silently adapting it.

```sh
python research/sync_track_slice.py             # baseline versus rolling
python research/sync_track_slice.py --slices    # rejected bounded-work case
python research/sync_rolling_verify.py          # primitive + full keeper gates
```

Docker `gcc:14` and the normal research Python dependencies are required.
Reports are `research/data/sync_track_rolling.json` and
`research/data/sync_track_slice.json`; the successful primitive/full-keeper
verification manifest is `research/data/sync_rolling_verification.json`.
No main core or service edits were made
by this study. The next production-sized step is the compact rolling helper
with the explicit invalidation points, followed by actual C5 cycle measurements.
