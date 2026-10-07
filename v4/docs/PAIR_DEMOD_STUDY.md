# Two-bundle asymmetric pair FM prototype

Research extension of C5VRX by Twotoz and contributors, canonical repository
https://github.com/Twotoz/C5VRX and website https://twotoz.github.io/C5VRX/.
The official website provides the Discord invite. This builds on the project's
Phase8/span50, HC50, BitScrambler source models and detector study. Integrated as an experimental selectable live TX program in a PR stacked on
#183. No measured RF sensitivity, physical continuity or board timing claim.

## Architecture

VLP56 switches from a packed 1024x16 phase-arithmetic LUT to one 2048x8 LUT.
The raw decoder occupies indices 1792..2047 and encodes one Q4/I4 endpoint
into one of 56 nonuniform phase tokens. The previous endpoint is retained as
a 28-class token (current token >> 1). A direct lookup maps the 28x56 endpoint
pair to a six-bit DAC code. Its rows have 64-entry stride: 1792 bytes reserved
for the map, 256 for the encoder. Useful entries total 1824; padding consumes
224 bytes. The map is a bounded phase-frequency estimator, not a full
maximum-likelihood trajectory receiver. Its selected codebook contains phase
information; amplitude-aware token experiments were tried but did not win.

Two alternating bundles are unrolled across all eight slots:

1. Emit the previous pair-map result twice; address raw decoder at 1792+raw;
   read16 and write16. The second raw IQ sample is not decoded.
2. Address pair table at ((B >> 1) & 31)*64 + decoded_token; LDCTIB retains
   the address and therefore the current token in B[5:0].

One pair consumes two raw bytes and produces two equal DAC bytes. Geometry
is RX40, unique CVBS20, physical TX40. There are two lookups, two bundles,
one counter load and no per-sample CPU computation. Table entries implement
frequency-to-DAC scaling and bounded outlier handling directly.

## Evidence

The source-driven model checks all 65536 raw endpoint pairs in a continuous
stream and verifies duplicate DAC bytes. The generic model is locally extended
with the established eight-slot circular wrap. Assembly, FIFO behavior, timing,
8-bit LUT indexing on the live target, EOF and physical continuity remain to
be tested. Existing level/DC writers expect a different LUT layout and must
not be used with this prototype.

Synthetic channel/truth generation, goggle filter and error scoring come from
`tools/detector_study/designs.py`. Q4/I4 capture is simulated. Training-only
endpoint histograms use seeds 101,102 and C/N 6,8,10,14 dB. Codebook selection
used seeds 207,208. Final evaluation uses independent seeds 309,310,311.
Final scoring uses the existing quarter-sample lag/gain/offset fit, counts
error against the true video and uses the same five-MHz goggle filter.
No sync/line replacement is used.

| Input C/N | Adjacent40 SINAD | HC50 SINAD | VLP56 SINAD |
| --- | --- | --- | --- |
| 2 dB | 1.19 | 1.82 | 2.39 |
| 4 dB | 2.62 | 3.68 | 4.37 |
| 6 dB | 5.78 | 6.57 | 6.76 |
| 8 dB | 8.66 | 9.09 | 9.00 |
| 14 dB | 14.63 | 14.80 | 14.15 |

At 4 dB C/N: adjacent40 has 90.23 >40-IRE errors per 1000 samples; VLP56
has 50.46, a 44% reduction. Adjacent40 with its own clamp reaches 3.25 dB
SINAD and 85.90 errors/1000, so the gain over that reference is 1.12 dB.

The unmodified adjacent reference uses every captured sample and no DAC
quantization; VLP56 has endpoint decimation and a physical six-bit code.
Equal goggle bandwidth and total error scoring do not prove identical
frequency response or detail retention: explicit PAL/NTSC tone and edge tests
remain necessary. The 4 dB result is 1.75 dB OUTPUT SINAD gain, not measured
RF threshold gain or a range multiplier.

## Limitations / acceptance

The best weak-signal candidate loses 0.65 dB against HC50 at 14 dB input C/N.
It does not satisfy the requested equal-or-better-than-adjacent performance
at every operating point. The bounds assume a calibrated 6.7-MHz
sync-to-white frequency span and 1-MHz carrier offset in this test; other
VTX deviations, CFO, PAL chroma, gain/lane folding, interference and multipath
are not validated. The phase codebook needs real capture testing across gains.
A strong-signal mode could retain HC50, but safe live mode changes are unproven.

Run `python v4/tools/detector_study/test_vector56.py` from repository root.
`tools/vlp56_codebook.json` stores the selected encoder and clipped 0..63 table.
`vector56_codebook.py` reproduces the codebook sweep; the pinned candidate was
selected before final seed evaluation. New sweep rankings may differ slightly
because six-bit saturation is now enforced during scoring as well.


## Integrated stacked build

`tools/generate_vlp56.py` deterministically generates the actual native firmware
program from JSON without NumPy/SciPy. `generate_phase8.py` invokes it in CI.
The active VLP56 file is separate from every historical span75 output, and
configuration-time verification is fatal again. Native HC50 is a separately
pinned copy of #183's checked-in source, so CI cannot replace it with Unwrap75.

An absent/invalid `c5vrx4/ref_demod` boots VLP56 in this experimental PR. Existing
values 0/1/2 select HC50/HR50/Golden50. `g` or SETUP -> DEMOD (REBOOT) cycles
HC50 -> HR50 -> Golden50 -> VLP56 -> HC50 and reboots after saving NVS; the
first `g` from VLP56 therefore returns to HC50. Menu exit reloads the same
selected program. The OSD inactivity timeout remains removed.

Louis Hitchcock's staged recovery from #182 (`784bbe6d625ab17ec115a7d0d1da57a1c45dcd26`)
is applied to all four selectable span50 modes. It bypasses the hard-G20 severe
overload shortcut, honors settling/freshness, walks adjacent physical tuples
upward after overload, and retains high-gain real-loss listening. It is not
applied to manual/native gain ownership. Regression coverage is imported with
attribution; the quoted report of solid range/video is evidence for the donor
recovery, not for VLP56. PR #184 corrects the tested program to Unwrap75
STD150 with flywheel off (the old generator overwrote HC50). Its corrected
board data are preserved in STAGED_GAIN_SPAN50.md. The recovery now has its
own always-on hook, independent of the demod/layout compatibility gate.

All span75 semantic estimates, AUTO AFC/search, history/mask, flywheel/line
repair, idle raster and live level/DC LUT writers are gated as in #182. Stored
preferences are retained, and unavailable menu options show N/A. Native gain
ownership, RF DC holds, lane defaults, filter calibration and transport remain.
VLP56's fixed study transfer is selected with the program, not with the old M
CVBS mode. Scope-check actual sync depth/offset and calibrate the VTX CFO and
6.7-MHz deviation assumption before interpreting picture/range results.
