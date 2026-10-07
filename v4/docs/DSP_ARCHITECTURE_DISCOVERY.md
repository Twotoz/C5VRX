# Hardware-first demodulator architecture discovery

Extends C5VRX by Twotoz and the C5VRX contributors, including the existing
phase, trajectory, finite-state and PLL studies: https://github.com/Twotoz/C5VRX.
The official website and Discord invite are at https://twotoz.github.io/C5VRX/.
Existing donor credits and GPL-3.0-only remain. No independent invention,
global optimum, physical range gain or hardware picture acceptance is claimed.

## Result

The reproducible run evaluated **200,000 distinct compiled LUT behaviours**
from 503,072 proposals. It rejected 124,728 invalid/unsupported proposals and
deduplicated 178,344 equivalent behaviours before evaluation. There were
3,200,000 tone measurements, 40,553 candidates reaching waveform screening,
162,212 short waveform tests and twelve frozen full-frame finalists. Screening
took 424 seconds on this host; repeated equivalent trees are not counted as
new candidates. Counts and seed/source protocol are in
`data/dsp_search_200k/search_summary.json` and `protocol.json`.

Selection chose DSP-9c5463dcb322. Independent nominal confirmation rejected
it for weak sync regression; its mean weak SINAD was 3.0265 dB versus OVP56
3.0333 dB. Echo/fading confirmation also rejected it: 0.6947 versus 0.8500 dB.
There is **no confirmed new winner** and no runner-up was substituted after
seeing final results. OVP56 remains the safe default. The floating IQ40 PLL
retains promising nominal results but is not a compiled continuous C5 design.

The PLL96 board failure also exposed a real reversed-IQ encoder bug. See
[PLL96_LAB.md](PLL96_LAB.md): the original board build is quarantined; its
corrected and retuned loop is an explicitly requested LAB comparison only.
Recovering sync pulses in synthetic strong video is useful, but does not make
its remaining waveform distortion acceptable as the default.

## Implemented search space

`tools/dsp_search/expressions.py` defines bounded expression trees with a
shared radians-per-50-ns-span type. Leaves represent wrapped phase differences,
complex-conjugate phase, cross products, ratio/polynomial/piecewise/sign
approximations, midpoint context, a projected OVP component and quantized
history. Operators mix, confidence-weight, clip, shrink, saturate or predict
bounded phase innovations. Coefficients and depth are checked; mutation and
recombination retain the dimensional contract. Confidence is a dimensionless
offline feature used to compile the table, not an extra runtime computation.

Sixty-five source-compiled templates include the existing pair layouts,
middle-bit context and a four-state frequency-history transition. Code and
physical-conductance reconstruction are explored. The OVP component is an
offline projection into each template's available tokens; the resulting LUT
does not secretly access a second detector. This is constrained evolutionary
architecture search, not unrestricted symbolic invention. Confidence and
prediction trees can be equivalent; compiled behaviour hashing removes that
duplication. Higher-order floating PLLs, Kalman filters and arbitrary history
are not silently assumed to fit: unsupported schedules are rejected.

## Hardware gates

`hardware.py` invokes the actual pair/state compiler before expensive scoring.
Supported generated programs use LUT8/2KiB, eight instruction slots, two LUT
lookups/two bundles per 50 ns, raw eight-bit IQ40 and a raw32KiB DMA ring. Unique
DAC20 is duplicated at physical DAC40. Four-state history consumes the two
remaining LUT output bits after DAC6. Input IQ remains I-high/Q-low signed
nibbles. No CPU sample processing, transformed ring, simultaneous RX/TX
BitScrambler or boundary state reset is introduced. Unknown schedules fail.

The separate PLL96 implementation uses LUT16 and 96 joint states with eight
observed tokens. At its original 11.531537-MHz span, 100/50/25-kHz frequency
spacing needs 117/232/463 frequency bins; with eight phase states this would
require 936/1856/3704 joint states and exceed its LUT budget. This rejects that
absolute-state layout, not every possible residue, dither or compressed scheme.
Source feasibility is not measured hardware throughput or goggle acceptance.

## Evaluation and evidence boundaries

The early screen uses signed tone transfer, absolute levels and jitter, then
four predeclared 16,384-sample PAL/NTSC active/H-sync proxies. It does not claim
vertical qualification. Twelve finalists are frozen before independent
full-frame selection (seed9401). One candidate is frozen before final seeds
9501/9502 and echo/fade seed9601. Search seeds are 9101/9201/9301.

Full raster tests contain two interlaced fields: PAL 1,600,000 IQ samples,
NTSC 1,334,668. H-sync, equalizing and broad vertical pulses, blanking, black/
white patches, burst, chroma and fine luma detail are included. Timing follows
the project's `main/menu_raster.c` and [ITU-R BT.470](https://www.itu.int/rec/R-REC-BT.470)
and [BT.1700](https://www.itu.int/rec/R-REC-BT.1700). NTSC models zero setup;
these are synthetic engineering stimuli, not certified encoders or every
regional CVBS variant. Source bandwidth is 6 MHz, the existing receiver-channel
model is retained, and IQ is acquired at 40 MS/s. C/N is simulated, not input dBm.

Every candidate receives the identical IQ. A single positive OVP56 noiseless
calibration is shared; no candidate/noisy gain, polarity, offset or delay fit
can conceal bad sync. Metrics include waveform SINAD, signed contrast,
horizontal/vertical pulse misses, vertical trains, edge jitter, pulse widths,
sync-tip/black/white errors and large excursions. Strong guards cap SINAD loss
at 0.25 dB, additional level error at 2 IRE and additional timing error at
0.1 us, with no additional missed pulses. Weak guards require no additional
misses and at most 0.1-dB SINAD loss. Promotion additionally requires positive
mean weak improvement in both fresh nominal and stress tests. Relative gates
do not imply that a very weak baseline itself has usable sync.

`large_errors` is excursions per 1000 samples; `raw_min/max` in the waveform
CSV are calibrated IRE extrema, despite their historical field name. The
loaded DAC voltage columns use the resistor/load model; no voltage was measured
on the board. Echo/fading includes a delayed complex path, periodic attenuation
and IQ skew/DC. Real RF-channel coverage and actual goggle compatibility remain
unproven. No real contiguous IQ capture was supplied for this run. Capture
replay exports observational traces, without inventing ground truth or SINAD.

## Reproduce and inspect

Use Python3.13 and `pip install -r tools/dsp_search/requirements.txt`, with
OPENBLAS_NUM_THREADS=1 and OMP_NUM_THREADS=1. From `v4/`:

```sh
python tools/dsp_search/test_discovery.py
python tools/dsp_search/engine.py --output /path/search --evaluations 200000 --shortlist 12
python tools/dsp_search/validate_search.py --search /path/search --output /path/confirmation
python tools/dsp_search/leaderboard.py /path/search/leaderboard.sqlite --status proxy_eligible --limit 20
python tools/dsp_search/audit_pll96.py --output /path/pll-audit.json
```

Outputs include a searchable SQLite leaderboard, all eligible Pareto tradeoffs,
frozen mathematical models, compiled finalist BitScrambler programs, protocol
and detailed per-frame CSV comparisons. `--captures DIRECTORY` on validation
accepts contiguous packed-IQ `.raw` files. Full leaderboards are kept outside
the firmware tree; concise evidence is committed under `data/dsp_search_200k/`.
The Pareto dimensions are weak SINAD, strong loss and pulse misses; twelve
highest-weak-score eligible models, rather than every frontier member, receive
full confirmation. This limitation is explicit.

Regression tests compare accelerated execution with the independent source
BitScrambler interpreter for every supported template, reject unsupported
resources and dimensional types, check full raster lengths/vertical trains,
reject inverted or stripped sync and prove the original PLL I/Q reflection.
Normal firmware verification also checks all PLL startup states and bytes,
long-stream state continuity, DAC duplicates and safe saved-mode handling.
