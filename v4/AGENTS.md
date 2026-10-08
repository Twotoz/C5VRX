# Isolated C5VRX-4 integration ledger

## Operator-authorized range-priority options

`docs/RANGE_PRIORITY_STUDY.md` records the new explicit range-over-detail
objective and four completed million-evaluation runs (unique within each run).
Pinned options start at value8; Y cycles RANGE32 and enabled generated options,
saves and reboots. R from any range LAB returns to OVP56; g remains0..4.
RANGE MAX LAB failed the operator's board test (noisy/desynchronized);
restoring RANGE32 immediately restored a good picture. Value8 is quarantined
to RANGE32 and Y skips it. Also preserve strong-echo/PAL-recovery vetoes.
RANGE BAL LAB passes independent synthetic confirmation; board acceptance
is pending. `docs/SAFE_RANGE_STUDY.md` predeclares the next fresh search,
with fine ADC at every stage and explicit waveform/burst/timing guards.
Do not start C5VRX-5 until the operator says to do so. Preserve
value7/current RANGE32 and value4/OVP56 rollback, all live-writer gates,
continuous raw DMA/state, fixed lanes and gain ownership. No merge authorized.

## Operator-authorized RANGE32 lab

`docs/RANGE32_RESULTS.md` supersedes the earlier no-confirmed-replacement
research result: OVL-49c57570d609 passes independent synthetic PAL/NTSC,
echo/fade and amplitude/offset guards. Physical acceptance remains pending.
RANGE32 is value7, uppercase R toggles7/4 with saved-NVS reboot; ordinary g
still cycles0..4, OVP56 remains default and saved failed PLL96 value5 remains
quarantined. The winner is phase32/observation32 with analytical transitions
and learned DAC; it does not use teacher state/context/counter prototypes.
Preserve all existing DMA, gain ownership, live-writer gates and attribution.
Do not infer measured range or global optimality from the1.9million completed
evaluations (per-run deduplicated, cross-run overlap possible).

## Operator-authorized PLL96 lab

`docs/PLL96_LAB.md` records the failed physical value5 experiment. Its I/Q
encoder was reversed; saved value5 now falls back to OVP56. Value6 is the
operator-requested corrected 16-phase/6-frequency LAB, selected only through
uppercase P (also toggles back to OVP56). Lowercase p is the snapshot command;
g cycles safe values0..4. The repaired LAB recovers synthetic strong H/V pulses
but still fails strong-picture guards. Preserve this distinction and physical
failure evidence; build/dataflow proof is not physical RF acceptance.
`docs/DSP_ARCHITECTURE_DISCOVERY.md` records 200,000 unique compiled behaviours,
typed formula search and frozen full-frame confirmation. No replacement was
confirmed. Keep OVP56 default and the fixed positive reference calibration.
Record flash and board results separately. No live CPU demod or DMA-boundary
state resets are authorized. This supersedes the absence of a compiled PLL
candidate recorded in the previous research ledger, not its evidence bounds.

## Weak-signal research (PR186)

`docs/MEGA_DEMOD_STUDY.md` records 544 hardware-fit candidates and 100,000
parameter configurations across eight offline theory families. Frozen PLL
improves weak synthetic video but fails strong-signal guards; the selected
periodogram fails echo/fading and high-C/N confirmation. No theory is promoted.
WVP56 is a pinned research LUT with a tiny benefit, not a selectable mode.
Preserve signed Q4/I4 decoding and unique scenario identities in score pairing.
`replay_c5_iq.py` compares actual contiguous IQ captures on the host; it is not
a continuous C5 PLL implementation. Defaults and the live hardware path stay
unchanged. Do not infer physical sensitivity or goggle-lock gains from these
short synthetic cases or from snapshot replay. Retain C5VRX/Twotoz/contributor
and existing adjacent/trajectory/PLL research provenance.

## Current stacked pair-FM experiment (on PR #183)

All selectable demods in this branch are two-bundle, 50-ns endpoints:
OVP56 (new/invalid NVS default), VLP56, HC50, donor HR50 and donor Golden50.
VLP56 uses LUT8/2048 bytes, current56/previous28 tokens, raw Q4/I4 RX40,
raw32K ring, unique CVBS20 and [D,D] physical TX40. CPU does not pace samples.
`g`/SETUP DEMOD cycles with a saved-NVS reboot; 0 remains the HC50 baseline.
Preserve Louis Hitchcock's staged overload recovery imported from #182
`784bbe6d625ab17ec115a7d0d1da57a1c45dcd26` and its regression tests.
PR #184 (`fa47bed`) supplies the independent always-on staged recovery hook
and corrected board evidence in docs/STAGED_GAIN_SPAN50.md. The accepted
#183/#184 board test ran Unwrap75 STD150 with flywheel off because the old
generator overwrote HC50; do not call it HC50/VLP56 board acceptance.
Span75 semantic estimates, AUTO AFC/search, mask/history, flywheel/line repair,
idle raster and level/DC live LUT writers must stay gated for all selections.
OVP56 uses the same encoder/layout with a bounded offline video fit and clean
absolute DAC-level constraints. Preserve VLP56 as value3 and OVP56 as value4.
See docs/DEMOD_OPTIMIZER.md and its negative search results; no global optimum
or measured range claim is established.
Offline broad search (docs/BROAD_DEMOD_SEARCH.md) uses no VLP initialization
or transfer constraint: 62 LUT8 layouts, 2048 attempts/1997 unique fitted
models, matched Unwrap75 reference. No new finalist is promoted. Do not call
this an exhaustive architecture search or global optimum; new signal objectives
require fresh holdouts. Firmware defaults remain those above.
This supersedes the historical Unwrap75 defaults/invariants below for the
active pair-FM path; the old programs/tests are retained research artifacts.
The generator must not overwrite native HC50 or substitute span75 for VLP56.
See docs/PAIR_DEMOD_STUDY.md: source-model and synthetic-video results do not
prove board timing, PAL/NTSC compliance, goggle acceptance or RF threshold.


Operator-authorized reference gain comparison, 2026-10-07: HR50/Golden
reference modes use staged overload reduction and physical-step recovery
from post-overload near-origin IQ, rather than immediate G20/max swings.
Unwrap75 retains its severe overload floor. Host evidence and Louis's positive
HR50 flight acceptance are in docs/V3_BENCHMARK.md; no measured dB gain is proven.

Operator-authorized benchmark comparisons, 2026-10-07: `g` selects isolated
two-bundle Phase8 HR50 or Golden Phase5/50 reference paths (NVS ref_demod).
Their unique20M [D,D] TX40M geometry is an explicit scoped exception to the
three-bundle Unwrap75 contract below, not a default replacement. Span75
sync estimates, AFC actuation, mask/repair/idle and LUT writers must stay
disabled in reference modes. Preserve donor provenance and exact DAC mappings.

Operator benchmark, 2026-10-07: `docs/V3_BENCHMARK.md` records the visibly
better standalone V3 configuration and the requested V4 quality target.
Use existing protected adaptive lanes as the first comparison, with Unwrap75
unchanged, at matched R3 tuning. This is not a production-default change or
physical acceptance. Preserve the known-good V3 rollback and evidence bounds.

Local V5 follow-up, 2026-10-07: `docs/POST_DROP_SETTLING.md` records the
PR181 post-overload no-carrier settling reproducer and scoped guard fix.
The repeat flight retained gain swings and picture faults: physical picture
acceptance failed. Keep the scoped host regression distinct from live benefit.

Inherit the repository attribution, hardware and evidence instructions. This
directory extends C5VRX by Twotoz and contributors; README.md and docs/INTEGRATION.md
define its scoped defaults, donor lineage and pending physical acceptance.

Only this directory may change for this integration. Do not edit shared main,
root configuration, workflows or website to make the isolated build work.
The operator-authorized move to v4/ updates root documentation, the V4 CI
paths and firmware input hashing; keep those integration references in sync.
Its main/ snapshot is intentional isolation; C5VRX-3 remains untouched. Keep
subsequent donor updates explicit in docs/INTEGRATION_SOURCES.json.

Record source format, acquired rate, CPU/hardware ownership, raw ring, three
bundles, unique 13.333M output and physical DAC40M separately. No CPU per-sample
output, transformed ring, concurrent RX/TX BitScrambler or boundary DSP resets.

Fixed fine lanes {9,7,6,5} are default (Leon, 2026-10-04): never switch lanes
at runtime. Fixed ultrafine and protected adaptive V5 lanes are Z comparisons.
Native is opt-in and owns gain exclusively. Under native the IQ tap is the
coarse lane set for the whole session and the restart patch 71C4[25:23]=7
is applied in every PHY restore (NVS native_patch, default on; board
measurements 2026-10-06 in the native AGC lab commits). Severe coarse or fixed-lane clipping
may cut active Direct Gain to its established G20 floor; fresh epochs and settling
refusal still apply. Manual/native never use this automatic gain response.

Sync scoring is a stride-3 semantic estimate, not tagged physical DAC output.
AFC uses adjacent raw Phase8 burst/clean-porch evidence and separate sticky video
TRACK; AUTO remains off until explicitly selected, with a bounded correction
budget. Do not use gain HOLD alone as a video lock or a CFO-write permission.

Leon explicitly authorized default-on sync-referenced CVBS level regulation on
2026-10-04 in PR164. The adaptive 5/20-ms observer uses completed IQ snapshots and bounded
DAC-only LUT16 updates after a stopped-engine addressing probe. Preserve the
explicit u opt-out, noise/context/settle refusal, loss hold and fault latch. RF SETTLE and lane-history guards must precede
qualification; recovery is bounded to 100 ms, slew to 32 mV in the loaded table.
Live RAM arbitration, waveform seams and FIFO continuity remain physical gates;
do not claim their proof from default-on authorization or host tests. H/V
regeneration and pre-Q4 (PHY) DC correction remain absent outside the reversible `#`
lab. Leon authorized default-on digital DC recentring of the static decoder and
a first-lock sampling-phase check on 2026-10-04; keep their opt-outs, refusal
conditions and verified LUT path. Leon also authorized a fixed, measured analog
RX filter width (target 24 MHz) replacing the BW20/BW40 gear on 2026-10-04.
It extends ESPARGOS esp-sdr's C5 BANDWIDTH control (absolute regs 6/7 code,
noise-FFT curves, commit ac627b0b) and zerowidth PR #3's 11p noise result;
keep that credit, keep regs 8..13 and upper bits calibrated, calibrate only
without a carrier, never go below the 24 MHz floor that C5VRX's BW20 test
motivated, and keep the `^` opt-out. Where the chip cannot reach 24 MHz at
all (first board, 2026-10-06: 19.4 MHz at every RX0 code), Leon decided the
same day that the second stage may narrow the normal profile by at most 7 %
of the delivered width for >= 0.3 dB lower noise bandwidth, and that the
range-edge gear uses the second stage (>= 14 MHz, >= 0.3 dB). Leon asked on 2026-10-04 for the native AGC
acquisition mask (docs/NATIVE_AGC_MASK.md): native mode only, witness bit measured
on the board (never guessed), STATIC decode, six BitScrambler slots, three
bundles per span on every path; keep the `|` opt-out, the per-boot latch, the
pacing exclusion and the DC-recentring refusal (bank 3 is the hold plane). The
no-carrier idle raster (docs/HDZERO.md, 2026-10-04) only replaces demodulated
receiver noise: keep it out of any state with a carrier or sync, exit at the
first one, keep V5 at its no-carrier maximum while it owns TX, and keep the `_`
opt-out. It is not sync regeneration of a received picture. Leon asked on 2026-10-06 for line repair in
the sync flywheel (docs/SYNC_FLYWHEEL.md): `LINE REPAIR`, default on with the
flywheel since the same day (operator), menu opt-out, needs the
flywheel, copies only to bytes TX has not read and only from bytes RX has not
overwritten, never in the vertical interval, at most 6 lines in a row; keep
its dropout criterion (score >= 8, >= 7 worse than a clean source), which the
host model needs so the uniformly weak range edge is not made worse. The V5
radius boost (docs/RADIUS_BOOST.md) only moves the healthy P50 band on a strong,
tight, rail-free ring; keep the immediate exit on rail codes/P95/jumps, the
doubling hold-off, the normal-band constants and `y` as an opt-in. The
level servo runs under native AGC too (docs/HDZERO.md): keep its phase-domain
validity gates, and keep CPU snapshots on the Q3 mask decode while masking. Live LUT
writers must stay rare (HDZero): keep the level servo's settled hold (24 mV /
250 ms), DC recentring's 10 s gap and the idle raster's exit hysteresis and
re-entry hold-off. Run tools/verify.py and exact-head
IDF CI before requesting merge approval; merge only after Leon approves.
