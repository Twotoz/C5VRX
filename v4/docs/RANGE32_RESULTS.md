# RANGE32: independently confirmed synthetic video, pending board acceptance

This work extends C5VRX by Twotoz and its contributors, including the existing
OVP56, trajectory and PLL research. Source: https://github.com/Twotoz/C5VRX;
official website and Discord: https://twotoz.github.io/C5VRX/.

## Decision and safe operation

The best independently confirmed candidate in this experiment is
`OVL-49c57570d609`, exposed as **RANGE32 LAB**, saved selection7.
Uppercase serial `R` toggles RANGE32/OVP56 with a saved-NVS reboot.
OVP56 remains the new/invalid-selection default. Ordinary `g` cycles only
the existing safe modes0..4. Uppercase `P` retains corrected PLL96 LAB6;
the failed original PLL96 selection5 remains quarantined to OVP56.
No automatic promotion, merge or physical video acceptance is implied.

RANGE32 passed independent full-field PAL/NTSC waveform, synchronization,
fine-detail, amplitude, carrier-offset and echo/fade guards. This is evidence
for a hardware experiment, not a claim of measured FPV range or best-ever FM
demodulation. No new real IQ recording or goggle comparison was available.

## Winning mathematical architecture

The winner has 32 phase states, 32 centroid-derived IQ observation tokens,
one frequency state and no confidence groups. It is a first-order phase
tracker, not a frequency-integrating second-order PLL. At unique20MHz:

```
prediction = phase + 2*pi*866863.762224349/20000000
error = wrap(observed_phase - prediction)
innovation = clip(error, -0.776771212515243, +0.776771212515243)
gain = min(1.8, 0.753875557984854 * (1 + 1.3681383367630984*abs(error)/pi))
phase_next = Q32(prediction + gain * innovation)
```

The initial output blends continuous and quantized phase advance with mixing
weight0.3850514728742406. Offline sparse least-squares reconstruction then
learns the DAC6 field against actual filtered CVBS, with strong-signal weight32,
edge weight8, regularization0.1, 24 solver iterations and blend0.25. The exact
bounded equations, encoder rotation and quantizers are in
`tools/dsp_search/overlay_fsm.py`; `tools/range32_model.json` pins all parameters
and the final learned words. `tools/generate_range32.py` independently
regenerates encoder/state fields with scalar standard-library mathematics and
checks the full model digest before generating the firmware source.

The winning encoder/state transition is analytical. It does **not** use the
experimental next-sample sign context, counter residue or teacher-fitted state
transitions. Those alternatives were explored and retained as research, but
did not produce a better independently confirmed model. Unused frequency-loop
parameters in the serialized common schema do not create hidden state.

The shared-word LUT uses 1024 16-bit words: low6 bits DAC, next5 bits state,
high5 bits decoder token. Decoder addressing and transition addressing share
storage without overlapping fields. The generated schedule retains eight
instructions, two bundles/50ns, two LUT lookups, 2048 bytes LUT, raw32K DMA,
TX-only BitScrambler, raw IQ40 and duplicated DAC6 [D,D] at physical40MHz.
There is no CPU demod loop, transformed ring or DMA-boundary state reset.

## Frozen independent confirmation

Selection used seed19301 and amplitude/offset/echo screens19351..19355 before
freezing the candidate. Final nominal seeds19401/19402, echo/fade19501 and
amplitude/offset19511..19514 were independent. Source imagery also changes
with each seed, rather than testing only different noise on identical bars.
All detectors receive identical IQ bytes and one positive OVP56 reference
calibration; no candidate-specific polarity, gain or delay fit is permitted.

| Independent metric | OVP56 | RANGE32 |
| --- | ---: | ---: |
| Mean weak nominal waveform SINAD | 6.782dB | 7.677dB |
| Weak nominal missed H+V pulses | 8748 | 2347 |
| First passing tested nominal C/N | 22dB | 14dB |
| Mean weak echo/fade waveform SINAD | 2.924dB | 3.260dB |
| Weak echo/fade missed H+V pulses | 4489 | 3017 |
| First passing tested echo/fade C/N | none | 30dB |

Weak means aggregate C/N0,4,6,8,10 over PAL/NTSC. The nominal threshold is
a discrete engineering-screen result (nearby tested points10,14,18,22),
**not an 8dB measured RF sensitivity improvement**. Miss reductions are
73.2% nominal and32.8% echo/fade; at very low C/N neither detector necessarily
gives usable video. Floating-point PLL remains substantially better in weak
SINAD and is a useful teacher/control, not an executable C5 sample path.
The retained CSV includes VLP56, HC50 and original/repaired PLL96 controls.

At nominal strong C/N30, average PAL/NTSC SINAD changes respectively
15.960/16.094dB to16.081/16.181dB. Fine-detail correlations change
0.7276/0.7463 to0.7221/0.7393: small losses within the frozen0.03 guard.
Maximum H-width errors are about0.04us rather than0.02us. Passing these guards
does not establish identical physical colour or goggle compatibility.

Fresh integrity seed24601 checks actual coarse and fine folded ADC-bit models,
two RMS occupancies, carrier offsets0.5/1/1.5MHz and PAL/NTSC colour bursts.
All declared strong colour guards pass. Maximum strong burst-phase jitter is
14.98degrees versus16.32 for OVP56; burst amplitude gain spans0.744..0.856
versus0.805..0.901. Some chroma attenuation remains. These are synthetic
burst projections, not a colour-decoder or monitor certification.

Fresh fixed-gain carrier-outage seed18601 removes signal at7..8ms and26..27ms
while retaining noise. At C/N10 RANGE32 has zero missed pulses in the80
post-return lines versus21 for OVP56. The first five consecutive timing/width
valid lines are reached within0.1..201.9us; OVP56 does not meet this condition
within the first20 lines. At C/N6 neither reliably meets that lock condition,
despite fewer misses. This does not model analog AGC recovery.

## Breadth, negative findings and PR review requests

Four completed shared-word searches evaluated **1,700,000 distinct LUT
behaviours within their respective runs**:400k initial,500k detail,
500k corrected robust and300k channel. Together with the preceding200k typed
architecture run, this is1.9million completed evaluations. Cross-run duplicates
are possible; these are not1.9million independent architecture families.
An interrupted robust run is explicitly excluded. The large searches alone
did not confirm a replacement; nominal gains repeatedly failed detail,
echo/fade or offset guards.

An exhaustive2986-model structural grid followed by432 learned-output
variants also failed final offset validation. The subsequent teacher/output
study evaluated372 distinct models and128 output fits; its winning control
retained analytical transitions and learned the output from true CVBS.
Teacher traces use only received-IQ floating PLL state and remain offline;
ground-truth waveform supervises reconstruction, never firmware inference.

Additional next-sample IQ sign context uses actual raw bits with the same
eight-slot schedule. An address-coupled16-bit ADDCTIA residue prototype was
also synthesized and assembled, not assigned an imaginary free accumulator.
Its288-model grid plus216 learned variants produced zero eligible finalists.
It is not advertised as a25/50/100kHz hardware PLL. The ESP32-C5 counter
semantics are documented in the [Espressif BitScrambler guide](https://docs.espressif.com/projects/esp-idf/en/latest/esp32c5/api-reference/peripherals/bitscrambler.html).

Floating-PLL ablations independently disable confidence, phase/frequency
memory, quantize phase8/16/32/64, frequency25/50/100/1050kHz and change output
reconstruction. Coarse phase and frequency both damage strong waveforms;
frequency-only output improves weak SINAD while losing strong fidelity.
25..100kHz frequency grids behave similarly in this host test. Pair20 with
unchanged gains also loses quality, but changes loop bandwidth and is not a
fairly retuned architecture comparison. Full rows are retained, including
negative results; no ablation proves that one feature alone causes board noise.

A matched-work strategy pilot compares balanced brute force, random and
guided selection with two independent starts:64 unique structural proposals,
six capped output fits and18 blends per trial, followed by fresh full-field
confirmation. **All six trial winners fail at least one strong-picture guard.**
This supports no confirmed strategy winner. Cached proposal costs and actual
fit times are reported; equal operation caps are not exact equal CPU time.
The brute-force grid remains available alongside guided search. These bounded
experiments indicate saturation of the tested families, not global optimality.

## Evidence, reproduction and remaining physical work

[Evidence index](data/overlay_search/README.md) links protocols, frozen models,
selection and independent confirmation rows. The large searchable SQLite
leaderboards remain local regenerable experiment artifacts; the repository
contains compact decisions and per-frame CSV. See
[architecture and commands](OVERLAY_RANGE_SEARCH.md) and each tool's `--help`.
Dependencies are pinned in `tools/dsp_search/requirements.txt`.

Regression checks cover scalar regeneration, all8192 winner state/raw cases,
70k-sample continuity including DMA/ROM wrap, physical phase-ramp polarity and
absolute DAC levels, all five shared-word allocations, counter/context dataflow,
filter adjoints, teacher projection, sync metrics and signed ADC-bit folding.
The ESP-IDF6.0.2 firmware build, ordinary C regressions and generated-source
verification pass. Build/source proof is separate from measured board timing.

Before promotion, capture matched strong/weak/drop IQ and goggle video with
fixed source/channel/gain, compare OVP56 and RANGE32, and check PAL/NTSC
sync/colour/fine detail and recovery. Uppercase `I` plus
`tools/capture_iq_snapshot.py` exports guarded8190-byte IQ snapshots, but each
covers only204.75us with unknown initial state: insufficient for V-lock or
range claims. The coarse/fine synthetic lane model covers signed-bit folding;
actual analog ADC calibration, noise and AGC remain physical uncertainties.
