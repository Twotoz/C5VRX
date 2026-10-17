# Same-IQ OUTPUT comparison and optional AUTO bounds

C5VRX by Twotoz and the C5VRX contributors. This extends the existing
`output_level.py`, `u85q_lp.py`, real-snow keeper and field-walk recordings.
Canonical source: [C5VRX](https://github.com/Twotoz/C5VRX);
[official website and Discord invite](https://twotoz.github.io/C5VRX/).

This addresses the owner's
[AUTO minimum proposal](https://github.com/Twotoz/C5VRX/pull/212#issuecomment-6156589968),
[identical-IQ A/B/C request](https://github.com/Twotoz/C5VRX/pull/212#issuecomment-6166374209)
and [priority clarification](https://github.com/Twotoz/C5VRX/pull/212#issuecomment-6167109293).
Gain/DC/AFC/filter policy work remains the higher-priority RF investigation.
This comparison changes no production policy, default, menu, NVS or transport.

## Reproduction and evidence boundaries

```powershell
python research/output_abc.py --check
python research/output_abc.py
python test/test_raster_geometry.py
```

The study compiles `research/output_abc_replay.c` with Docker `gcc:14`,
links the actual root keeper and exports LUTs using actual `output_entry()`
and all eight checked-in Kp tables. Python uses the repository's U85Q and
U85Q-LP arithmetic models, previously checked against the ESP-IDF assembler
emulator. Each comparison starts at the same pair parity and decoder state.
The JSON records input IQ SHA256 and implementation file hashes.
The saved A/B/C comparison and malformed-source reproducer explicitly use
`make_case(..., legacy_raster=True)` to preserve their original inputs.
Their historical implementation hashes describe the original run, rather
than pretending that later source-file edits are the same implementation.

Every original IQ stream is replayed across 1x, 0.5x and the Sync-Priority
candidate, all eight Kp values, LP on/off and both pair parities. Synthetic
PAL/NTSC cases cover narrow/normal/wide deviation, 6/30 dB C/N and a bounded
noise replacement across a vertical interval. Each real walk snapshot is
evaluated separately; disconnected 8190-byte snapshots are never joined
into an invented field clock. Local binary recordings are ignored by git;
their paths, hashes, sample counts and available metadata remain provenance.

Synthetic source regions are **evaluation labels only**. They never enter
the keeper or authorize repair. Stable region interiors exclude 40 samples
around transitions and the first half of each acquisition. This is a
deliberately limited picture/detail comparison, not proof of every handoff
sample or every possible VTX waveform.

The artifact reports DAC code range, unique codes, output/worker rail
occupancy, stable sync/porch/white levels, white headroom, active code
difference and slope ratio, burst-region differences and false-sync crossings
relative to each mode's measured sync/porch midpoint. A rail code does not
by itself prove analog clipping. False-sync statistics are an offline
classification, not a model of the affected LCD's decoder.

The archived voltage columns assume 3.3 V GPIO, the
8.2k/3.9k/2k/1k/470/240 ohm DAC, a200 ohm series resistor and75 ohm termination.
The supplied `docs/hardware/c5vrx-dac-schematic.png` instead shows the200 ohm
resistor as a shunt to ground. That topology mismatch was identified during
the2026-10-16 sync-gate investigation. These voltage columns therefore do not
qualify the supplied circuit; the model and voltage artifacts need a separate
topology correction and rerun before comparison with physical voltages.
They are static circuit predictions. GPIO impedance, resistor tolerances,
coupling, cable and actual monitor loading are unmeasured. There is no
closed-loop voltage sensor and no physical 75 ohm or LCD acceptance claim.

## What the three modes mean

- **1x:** exact current mapping and reference decoder output.
- **0.5x:** actual production level 9 mapping, applied to LUT DAC bits before
  decoding/LP. Phase/token bits stay exact. Over all possible input codes,
  output spans 11..42 and only 32 distinct DAC codes remain. The operation
  reduces sync depth along with picture amplitude; reduced displayed noise
  amplitude cannot establish improved RF information or LCD acquisition.
- **Sync-Priority candidate:** the existing real-source-qualified keeper
  generates missing H/V timing into an offline raw-IQ ring with the ordinary
  1x LUT. It has no static per-code sync/picture classification and no active
  limiter. No cold generated clock is admitted. The existing synthesis is
  qualified for U85Q-LP only: the U85Q/nonLP rows explicitly pass through and
  report ineligible. Missing real V qualification also reports ineligible.

This is an actual algorithm comparison with a clear refusal path, not a
claim that three live modes are implemented. A short real snapshot can
demonstrate 1x/0.5x mapping while being insufficient to establish a real V
anchor for Sync-Priority. Refusal is retained rather than supplying oracle
timing. Global sync-depth medians are not an H/V pulse-completeness test;
use the separate keeper waveform suite for its specific fixtures.

## Measured offline results and a retained source counterexample

`research/data/output_abc.json` contains 1920 rows: 20 distinct IQ inputs,
eight Kp values, two LP states, two pair parities and three modes. All ten
real snapshots refuse keeper acquisition/repair; their 204.75 us duration
cannot establish a real field clock. Several narrow/weak synthetic sources
also lack qualified V timing. These are explicit limits, not successes.

| Actual LP output | Stable sync depth across synthetic cases | Nominal static DAC model |
| --- | --- | --- |
| 1x | 6–14 codes | 66–143 mV |
| 0.5x | 2–7 codes | 19–76 mV |

The half-scale worker mapping has no LUT saturation, and rail occupancy
falls to zero in these cases. This does not mean RF information improves:
stable active samples differ from baseline by up to 9.09 codes RMS with LP
and 9.26 without LP. Contrast and sync both shrink; the limited six-bit
output also loses approximately one bit of code resolution. Offline active
false-sync crossings at each mode's own tip/porch midpoint total 265740 for
LP 1x versus 270857 for LP 0.5x in this matrix. Lower voltage/no rail codes
therefore do not establish better sync classification, let alone LCD lock.

The three-field PAL V-loss case initially fails active preservation on all
eight Kp values/both parities. The minimal retained reproducer is:

```powershell
python research/output_abc_pal_v_repro.py
python research/output_abc_pal_source_audit.py
```

For seed 213, 30 dB C/N, deviation 1, 12 MHz filter and noise replacement over
samples 1520000..1760000, the kp 0.85 callback keeper repairs 150 H + 15 V.
It actually changes 1170 raw IQ bytes labelled ACTIVE, first at 1593627
(field 1.99203375, line 622 + 1307 samples, half-line 1245 + 27 samples), last 1594799.
The first changed active DAC samples are 1593632/parity 0 and 1593631/parity 1;
their raw input also changed. This is actual painting, not merely LP tail.
The original IQ SHA256 is
`7e4a002f241e8c64fd274576a14c6026a68e4c75f40ce06857f0a297b9b52daf`.

The independent audit establishes a **source-generator defect** rather than
mistimed keeper V on a fixture with the checked PAL field-sync geometry. The nominal pre-EQ slot is
half-line 1245, starting 1593600. `sim.raster()` fills both halves of normal
line h1244, increments h by 2, and skips the mandatory pre-EQ h1245. Its
waveform and ACTIVE labels extend into that slot. The primary standard
specifies 64 us lines, 20 ms fields, 2.5 H pre/broad/post sequences and alternating
line/half-line field anchors; the existing C waveform fixture uses the same
geometry with a five-half-line origin shift. See [ITU-R BT.470-5, Figure 2-1
and Table 1-2](https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.470-5-199802-S!!PDF-E.pdf).
The audit compiles and calls the existing C fixture's actual `v_kind()` and
checks all1875 half-lines against this independent boundary oracle. This
does not certify every analog level, burst or blanking-duration tolerance.

The actual `sim.raster()` default now visits the missed first equalizing
slot and clears its complete blanking interval. The identical skip also
affected NTSC half-line525: [667332,668604), with1192 old ACTIVE samples.
The bounded fix handles both standards and keeps the original full-line
random draws, so picture outside those skipped slots stays exact. First
fields are unchanged; multi-field inputs reaching the affected half-line
now change. Historical studies are not silently regenerated. Use explicit
`raster_legacy()`/`legacy_raster=True` to reproduce old evidence, and the
default source for new corrected studies. This concerns field-sync geometry,
not wholesale analog-source conformance or measured hardware improvement.

The audit supplies a **separate corrected source**, adding the actual
missing equalizing waveform before FM/filter/quantization. It changes 1200
source waveform samples and 1200 region labels in exactly that half-line;
the rest of the source raster is unchanged. Its IQ SHA256 is
`cd084cf442f0a36aaaa9c71f7715c83807846f69f648ca0df7bae60b9ff8912f`.
All 16 Kp/parity combinations then preserve every ACTIVE IQ byte and every
stable active DAC sample. The original malformed source and failing results
remain in the main artifact and `output_abc_pal_v_failure.json`; they are not
relabelled, suppressed or replaced. This does not establish preservation of
arbitrary malformed-source active content occupying predicted V blanking.

The main artifact also retains small burst-region decoder differences at
low Kp. Together with the separate transition audit, these prevent treating
this limited offline comparison as a universal handoff or colour-burst proof.

## Optional AUTO: concrete limits and proposed boundary

`auto_candidate()` is a research-only bound, with no actuator. It takes
qualified sync depth and a conservative white span, a user minimum and
ceiling, and an acquisition/settled/evidence admission. Missing evidence,
ordinary TRACK, fades or rescue hold the last verified value. Its example
target is 18 DAC codes of sync depth, with 3-code tip/white reserves.

For a 12-code source sync depth and 55-code total white span, a 2x floor
conflicts with the 57-code usable span: maximum safe scaling is about 1.036x.
The proposal refuses that adjustment. For a 2-code shallow sync with 60-code
white span, even a 0.5x floor and 5x ceiling cannot achieve the 18-code sync
target: headroom limits scaling to 0.95x and predicted depth to 1.9 codes.
A multiplier floor therefore cannot guarantee minimum sync voltage.

An eventual optional mode should retain the user's persistent floor/ceiling
separately from runtime selection, require fresh matched-context real sync,
porch and defensible white/burst headroom, and qualify a stable candidate
at acquisition. The existing `output_level.py` research guard supplies a
starting hypothesis of 2 s agreement and 5 s minimum between applications, not
physical acceptance. Refuse a floor/headroom conflict; do not silently lower
the floor or continuously raise output during a fade. A failed verified
transport restart must leave the previous published level and state.

There is no evidence here to promote AUTO or Sync-Priority to production,
change the 1x default, or infer a dB range improvement. Loaded CVBS and actual
LCD lock/recovery across the same controlled RF input remain separate tests.
