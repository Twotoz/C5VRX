# Range-first follow-up: three experimental picture trade-offs

This extends C5VRX by Twotoz and the C5VRX contributors and the existing
RANGE32/OVP56/PLL work: https://github.com/Twotoz/C5VRX. The official website
and Discord invite are https://twotoz.github.io/C5VRX/.

The operator reports that flashed RANGE32 retains an excellent picture and
clearly improves range. This is positive qualitative hardware feedback, not
a matched attenuation sweep, numerical RF sensitivity result or global-optimum
claim. RANGE32 model49c57570d609 from84d6e391 was flashed with verified hashes,
NVS/PHY backup/preservation and a confirmed RANGE32 boot/heartbeat on COM10.
App4.0.0-alpha-range32 has ELF SHA256
69bb16283bcf08a2f453697e95a1b210bfce690e3bca71c7eb7e05d57672638b.

The operator now explicitly prioritizes maximum range over picture quality.
This **new** study allows intentional strong-signal detail/colour losses that
would fail the previous RANGE32 confirmation guards. Those earlier results
and quality requirements remain historical evidence; they are not rewritten.
OVP56 remains default, R remains the rollback, and the currently flashed
RANGE32 is left active during offline research. No automatic promotion/merge.

## Declared options and common range screen

### Completed runs and initial firmware options

All four million-evaluation searches completed. Each run deduplicates actual
compiled LUT/schedule behavior; overlap between runs is possible. Only the
screened subset receives expensive waveform tests, not all four million.
The independent final PAL/NTSC cases use two fresh image/noise seeds each.

| Pinned LAB | Final weak H/V misses, candidate/reference | Strong waveform SINAD, candidate/reference | Strong detail correlation, candidate/reference | Common usable C/N | Confirmation |
| --- | ---: | ---: | ---: | ---: | --- |
| RANGE MAX, f96c6225fc10 |465/3730|11.33/15.96dB|0.431/0.722|10/10dB|Vetoed; explicitly requested board experiment|
| RANGE BAL, c2510e3dad79 |2945/3709|15.36/15.98dB|0.713/0.721|10/10dB|Passes independent synthetic guards|

Weak totals cover C/N0..10dB, two seeds and both standards. Reference is the
same pinned RANGE32 on the same inputs, separately paired for each profile.
MAX misses15 versus341 H/V pulses at4dB and0 versus29 at6dB. Its nominal
aggregate reduction is87.5%; this is not a physical range percentage.
The common usability threshold remains unchanged because weak pulse widths
and timing still exceed the engineering bounds, despite many fewer misses.

MAX uses phase8/frequency32, four angular observation tokens and two bits of
next-IQ sign context; a bounded tanh innovation and learned DAC reconstruction
replace the old PLL96 equations. It has fixed loop gains, without radius
confidence groups or an innovation-dependent bandwidth multiplier. Tracking
still depends on its evolving state. The data does not require the more
explicit gain-adaptive variants to win every objective.

MAX improves echo/fade usable C/N18 versus30dB, but fails the predeclared
strong-echo waveform loss limit of2dB relative to RANGE32. Its weak PAL10dB
post-outage five-pulse lock takes512.1us versus320.1us:192us slower, exceeding
the allowed extra64us. Strong recovery is matched; these vetoes are preserved,
not removed to call MAX confirmed. The operator requested flashing this
candidate; these limitations were disclosed before flashing. It stays experimental.
Detailed rows and protocols are under `data/range_priority/`.

Firmware values8/9 pin MAX/BAL, while7 retains the physically tested RANGE32.
Uppercase Y cycles7->8->9->7 with saved reboot; R from any range LAB restores
OVP56 value4. No running LUT changes or automatic switching are introduced.
Extreme/compact confirmation is still running at this checkpoint; their
results will be appended separately, without altering the frozen selections.

| Profile | Strong waveform SINAD floor | Strong contrast | Fine-detail correlation floor | CVBS level-error limit |
| --- | ---: | ---: | ---: | ---: |
| Balanced |12dB|70..130%|0.40|10IRE|
| Range |9dB|60..135%|0.25|15IRE|
| Extreme |7dB|50..140%|0.10|20IRE|

All retain positive polarity and no missing strong H/V sync. Strong timing
and width tolerances progressively relax; exact predeclared bounds are in
`range_objective.py`. Chroma loss is measured and disclosed, not hidden by a
new noisy per-model calibration. These are engineering experiment choices,
not guarantees that every operator considers the result acceptable.

**The same usable-picture screen applies to every profile, OVP56 and RANGE32:**
waveform SINAD>=5dB, positive contrast50..150%, H misses<=2%, at most3
consecutive missing H pulses, no missing V pulses, two vertical trains,
H/V timing95<=0.5us and H-width RMSE<=0.5us. Its C/N threshold cannot be
compared directly with the old, stricter14-versus22dB screen. Fresh matched
RANGE32 and OVP56 controls are recomputed under this protocol.

The search emphasizes low-band luma information (700kHz) and missed sync,
allowing loss of high-frequency detail. Constant/erased, inverted and sync-less
outputs fail the guards. Final confirmation ranks the lowest passing C/N
before weak pulse losses and luma fidelity; luma SINAD alone cannot qualify a
model whose usable threshold or sync is worse than current RANGE32.

## Search and independent confirmation

Three independent1,000,000-evaluation runs use seed bases30000,40000,50000
for Balanced/Range/Extreme. Counts mean distinct actual LUT bytes plus token,
context and counter schedule within each run; cross-run overlap is possible.
Proposal, duplicate, tone, quick and detailed-screen counts are separate.
Evolution uses65% bounded parent mutations and35% fresh structural proposals.
Observation/state allocations, compact frequency memory and confidence groups
are explored;20% of fresh proposals use actual next-sample sign context and5%
use the previously compiled address-coupled counter schedule. Existing negative
counter/context results motivate bounded exploration rather than assumed gains.

A fourth1,000,000-evaluation run (base60000, Range guards) specifically explores
compact frequency-memory grids targeting25/50/100/250/500kHz state spacing.
Its predictor occupies a narrow band around the carrier; the innovation output
still reconstructs wide video excursions. It has at least two actual frequency
states, trading phase/observation capacity for memory, and uses the regular
compiled shared-word schedule rather than an imaginary fine accumulator.
This is a hypothesis, not instantaneous DAC6 or RF tuning resolution. Large
state allocations bound the span to the supported numerical band. Thus the
completed target is4million per-run unique evaluations, not four million
fundamentally different mathematical architecture families.

Stages are signed tone checks, approximate low-band information on five
8192-byte IQ cases, then detailed short-video checks for the top8 proxies per
topology plus random1/256 audits. The cheap proxy is not full CVBS acceptance.
Topology diversity is retained in the frozen finalists. Every candidate stays
inside the already independently interpreted/assembled C5 schedule family:
LUT16/2KiB/eight slots/two bundles/two lookups/raw32K/IQ40/TX-only/DAC6[D,D]40.
No CPU live sample processing, transformed ring, boundary reset or gain change.

Finalists receive six capped48-iteration DAC fits and three output blends per
fit. Non-counter models also test offline received-IQ teacher projections with
mix0/0.25/0.5; unsupported counter teacher projection is not invented. All
receive true-CVBS output supervision and the same fitting count/cap.
The current pinned RANGE32 is included as a control and refinement base.

Fresh full-field fine-lane PAL/NTSC selection includes offsets/occupancies
**before** freezing one option per profile. Final signals use bases+401/+402,
echo/fade+501, occupancy/offset+511..514 and carrier outages+531. Imagery and
noise both vary independently. A veto never substitutes a runner-up from the
same final data. Complete rows retain legacy PLL96, repaired PLL96, HC50 and
floating IQ40 PLL controls. Recovery checks compare actual post-return lines;
intentional carrier absence is not a healthy-video failure.

Final range-improvement labels require no worse common usable C/N and weak
sync than RANGE32, an actual threshold/sync improvement, acceptable echo/fade
and offset results, and no worse missed recovery lines. Experimental tables
remain available even after a veto, with that veto visible.
Recovery uses the same0.5us timing/width bounds for all options and controls:
first five valid consecutive H pulses among the first20 post-return lines.
If RANGE32 locks there, the option must lock within one additional64us line.

## Situation-dependent adaptation

The operator also requested a demodulator that adjusts itself to changing
reception rather than a single fixed quality/range compromise. The current
search already includes bounded, sample-dependent mechanisms: radius-token
confidence scales phase/frequency corrections, innovation magnitude adjusts
loop gain, and confidence can blend output toward the predicted frequency.
These decisions are compiled into the same immutable transition LUT. They do
not require CPU sample processing or live LUT replacement.

This is local adaptation, not an accurate instantaneous C/N measurement.
Large innovations can be noise, genuine video detail or sync transitions;
IQ radius also depends on gain, fading and clipping. Confidence groups spend
scarce observation bits, while frequency/history states spend tracking bits.
The search may therefore reject adaptation in favor of a simpler model. A
fixed LUT does not imply fixed behavior: its output and next state still
depend on the observation and current state.

For the compiled tracker family, the adaptive equation is
`e = wrap(observed_phase - predicted_phase)` and
`k = clip(kp * confidence * (1 + adaptation * abs(e)/pi), 0, 1.8)`.
The chosen bounded innovation function drives the quantized phase and frequency
updates; the final DAC mapping is evaluated separately. With no confidence
groups, confidence is exactly1. RANGE32 already uses innovation-dependent
gain, without an amplitude confidence partition or separate frequency state.
This is not a rule that every large error deserves a larger correction: the
search must demonstrate that the complete recurrence reconstructs usable video.
Hardware enforces `state_bits + token_bits = 10` and
`DAC_bits + state_bits + token_bits = 16`; extra memory/observations have a
real quantization cost rather than unlimited floating-point precision.

Existing independent echo/fade and carrier-outage cases exercise changing
conditions and recovery without resetting model state at a DMA boundary.
Passing them does not establish optimal adaptation to every channel. Any
subsequent slow policy that switches models would need separate hysteresis,
continuity and board validation; none is enabled by this search. No global
mathematical optimum or recovery of absent signal information is claimed.

## Reproduction

Use the pinned research environment, OPENBLAS_NUM_THREADS=1 and
OMP_NUM_THREADS=1. Output directories must be fresh. From `v4/`:

```
python tools/dsp_search/search_range.py --output /new/balanced --profile balanced --seed-base 30000 --evaluations 1000000
python tools/dsp_search/search_range.py --output /new/range --profile range --seed-base 40000 --evaluations 1000000
python tools/dsp_search/search_range.py --output /new/extreme --profile extreme --seed-base 50000 --evaluations 1000000
python tools/dsp_search/search_compact_range.py --output /new/compact --profile range --seed-base 60000 --evaluations 1000000
python tools/dsp_search/refine_range.py --search /new/range --output /new/range-refined
python tools/dsp_search/validate_range.py --search /new/range-refined --output /new/range-confirmed
python tools/dsp_search/leaderboard.py /new/range/leaderboard.sqlite --pareto
python tools/dsp_search/test_range_objective.py
```

Repeat refinement/confirmation for each profile. Large SQLite/teacher artifacts
stay outside firmware. Results and exact model identities will be recorded once
the running experiments complete. A synthetic label never replaces a matched
strong/weak/dropout IQ and goggle comparison; no measured dBm or range gain is
inferred without calibrated RF and the same source/channel/gain conditions.
