# Experimental D32-R2-LP

C5VRX by Twotoz and the C5VRX contributors. This extends the project's
`research/u85q_lp.py` counter-LP research and the exact D32-R2 table from
`research/nl_tracker.py` at `8b60fef0f43f43a5305d941568c5975386867fc0`.
The stock D32-R2 program, LUT and default are unchanged.

**Host implementation verified; physical performance unverified. The colour
and picture-lock results do not justify promoting this mode.** It is a
manual bench experiment, not an automatic range enhancement. In particular,
it does not reproduce a general +2–3 dB *luma* benefit from historical U85Q-LP.

## Schedule and exact arithmetic

MODEM_DIAG Q4/I4 -> PARLIO RX40 -> unchanged raw 32 KiB ring -> TX
BitScrambler -> unchanged six-bit resistor DAC, DAC20 duplicated at TX40.
Two instructions and two LUT16 lookups per 50 ns pair. Both free opcodes
replace stock NOPs; no extra read/write bandwidth, stage or realtime CPU DSP.
Four identical controller/worker pairs fill the existing eight-instruction
loader image. Each pair reads 16 bits and writes 16 bits.

| Bundle | LUT address/result | Counter operation | DAC output |
| --- | --- | --- | --- |
| Controller | Sample A's I4/Q4 at bits 16–23; bits 24–25 zero -> same D32 token | LDCTIA loads `(y>>1)<<10` plus raw address | A[15:10], previous y |
| Worker | Token L[15:11] at bits 16–20; phase from previous controller O[12:8] at bits 21–25 -> unchanged D32 phase and DAC | ADDCTIAH adds `(x>>1)<<2` plus phase[4:3] to A[15:8] | Repeat O[5:0] |

The controller carries previous DAC[5:1] in O[15:13] and O[7:6], and
phase[4:0] in O[12:8]. Bits 6/7 can differ between written bytes but have
no DAC pins. All six driven bits are duplicated. A[15:10] holds y; B is not
used (its mux region overlaps LUT16). Counter arithmetic takes output[31:16],
which shares its bottom ten bits with the LUT address.

**Why replacing only the LUT is incorrect:** legacy U85Q-LP loads sample B's
two signs into A[9:8]. Adding phase[4:3] in ADDCTIAH can carry into A[10],
so that filter includes address-dependent rounding/dither. D32-R2's token
ignores both signs: all four 256-entry token banks are identical. The new
controller therefore drives those two address bits low. A[9:8] starts at
zero, and adding the phase's two high bits (0..3) cannot carry into bit 10.
The complete 1024-word LUT is reused **without modification**, including
all next-phase, click-suppression and six-bit DAC values. The generator
asserts the repeated-token-bank property so a future incompatible table fails.

For each stock demodulated code x, the counter now computes exactly
`y = (y >> 1) + (x >> 1)`, unsigned with separately truncated terms.
The filter output has **one extra pair (50 ns)** of pipeline latency relative
to stock, in addition to its frequency-dependent IIR group delay. Startup
registers/counter are zero in the emulator; the production reset/start stage
runs on every restart. No reset occurs at descriptor, ring or program wrap.
Physical reset behaviour and transients still require scope verification.

The sum is at most 62, so no wrapping or saturation is required. This is
not `(x+y)>>1`: the separate shifts have a deadband and DC bias. Starting
from zero, constant positive even x settles at x−1, positive odd x>=3 at
x−2; x=0/1 settles at zero. Constant 63 settles at 61. This is an explicit
consequence of the requested formula, not compensated by silently changing
DAC scaling or the LUT. All manual and affine OUTPUT transformations still
act on the low-six-bit x **before** filtering via the halted, verified LUT
loader; the unchanged resistor/pin transfer then acts on y.

## Selection, status and recovery

Menu DEMOD and console `m` append `D32-LP` after D32. The full name is
`D32-R2-LP` (experimental flag in T diagnostics and documentation). NVS `demod3` is unchanged. Decoder IDs 0..3
remain P64/U85Q/U50/D32, LP appends 4; flight-log IDs 0..5 are preserved,
LP appends 6. The flight-log reader recognizes the new identity. D32 stays
the boot default; the U50 deviation fallback does not affect LP.

Existing stop/load/instruction-verify/LUT-verify/reset/start, five-second
guard, rollback and fault latch are reused. No RUN/PAUSE rewrite, live LUT
modification or automatic LP switching exists. On failure, program and LUT
return together to the last verified decoder. A failed live console selection
does not update NVS. Existing menu semantics are preserved: selection while
the menu owns TX saves the *requested* decoder, with verification on menu exit;
a later exit rollback restores live video but leaves that request saved for
the next boot. The runtime test covers successful menu exit, both-direction
stage failures, cooldown, fault latch and NVS boot restoration.

The existing sparse CPU picture observer receives the shared D32 LUT and
therefore reports **pre-filter** picture evidence/levels, not the new hardware
filter's colour or sync benefit. T diagnostics explicitly report
`picture_observer=pre_filter` when LP is selected. No CPU-per-sample LP was
added to make telemetry resemble the realtime output. AUTO OUTPUT remains
OFF by default; its existing observer still sees pre-filter levels when
explicitly enabled. Use manual 1.0x for controlled A/B testing. Treat those
observer values as pre-filter evidence, never as measured filtered quality.

## Reproducible simulation and definitions

[Full tables](D32_LP_RESULTS.md), [raw per-seed metrics](../research/data/d32_lp.json).
NumPy 2.3.5, SciPy 1.17.0, Numba 0.68.1.
Two complete fields per case, PAL and NTSC; C/N −2, 0, 2, 4, 6, 8, 12,
16, 20, 30 dB; deviation 0.7, 1.0, 1.4, 1.7; seeds 3202/3203/3204:
**240 identical-IQ cases, 720 decoder results**. Radius 4.2 IQ cells,
12 MHz extra analog filter, existing channel/DAC/goggle model. C/N is the
simulator's 20 MHz channel reference, not calibrated physical sensitivity.

This uses `sim.py`'s documented ideal normalization and clipped Q4 model,
not the optional physical folding ADC. It does not model gain transients,
DC faults, multipath or real receiver noise. Every decoder sees identical
noisy and clean bytes. The same clean affine gain/offset/delay fitting
procedure is applied independently to each decoder, then fixed for its noisy
output. This grants clean calibration knowledge unavailable in actual goggles.
The luma/detail filters retain the relative chroma/luma delay and amplitude
errors left after that one composite calibration; separate band refits would
hide those errors. Simulator reset/edge samples are discarded as in sim.py.

- **Luma SINAD:** 4th-order 2 MHz low-pass on output and truth, MSE on active
  picture. Separate from the existing wideband **composite SINAD** (which
  includes chroma and fine texture; historical studies sometimes called it luma).
- **Picture lock:** worse of ideal-AGC and peak-clamp `sim.goggle`, fraction
  of PLL-locked lines within 0.5 µs. This is not a measured PAL/NTSC decoder.
- **Usable threshold:** lowest sampled C/N where every seed has >=90% lock
  and >=6 dB luma SINAD, sustained through all higher sampled points. This
  is a declared engineering criterion with no interpolated dB/range claim.
- **Sync integrity:** fraction of true horizontal sync pulses matched by a
  −20 IRE sliced, 1 MHz filtered pulse of width 2–7 µs, falling within
  −1..+1.5 µs of the true edge (includes filter delay). Width MAE against
  the true width and median central tip depth are also recorded. VBI is in
  the source and goggle input; this metric itself measures horizontal pulses.
- **Burst:** per-line sinusoid/constant fits on inner burst samples, phasors
  rotated into the known reference phase (including PAL alternation), then
  coherently averaged. Gain is relative to the goggle-filtered truth burst;
  phasor SNR measures inter-line scatter. Fits use true burst windows,
  which a real unsynchronized decoder may not know.
- **Colour-killer risk:** explicit proxy: coherent burst <0.5 of truth or
  burst phasor SNR <6 dB. This is not an actual goggle colour-killer threshold,
  firmware decision or proof that any particular goggle loses colour.
- **Strong detail:** clean calibrated output RMS gain/error in 2.5–5 MHz,
  including chroma. Raw metrics also record fitted burst amplitude in IRE.

U50P64-R1 is deliberately a fixed comparison, without production's U80
fallback. Its poor results at deviation 1.4/1.7 must not be attributed to
normal firmware operation, which has that fallback.

## Findings and regressions

At 4 dB C/N, deviation 0.7/1.0, LP gives about **+0.13..+0.84 dB** of
2 MHz luma SINAD averaged over seeds. Composite SINAD gains are much larger
(about +3..+4 dB there); they are not interchangeable metrics. Across the
entire matrix the largest individual luma gain is +0.92 dB. The sampled
usable threshold is **unchanged** versus D32-R2: 4 dB for deviation
0.7/1.0/1.4, 6 dB for 1.7, both standards. No range gain is established.

Picture lock is not monotonically improved. At NTSC deviation 1.4 / C/N
0 dB, seed 3202, stock lock is reduced by **0.768** with LP. Across seeds
that cell averages about 0.74 -> 0.46. Both are below the usable threshold,
but this is a genuine modeled lock regression. Other near-cliff cases
improve; do not selectively quote them. Horizontal sync integrity generally
changes by only a few percentage points; the worst individual decrease is
0.0033, while goggle-PLL acquisition can diverge much more.

At C/N 30 dB, LP loses **2.5–7.4 dB luma SINAD** versus stock across
standards/deviations, and about **4.0–4.3 dB** clean 2.5–5 MHz detail gain.
It weakens the burst by an additional **5.2–5.6 dB NTSC** and
**6.5–6.7 dB PAL**. This follows the pole-1/2 response at a unique 20 MS/s:
ideal small-signal attenuation is ~5.1 dB at 3.58 MHz and ~6.3 dB at 4.43 MHz,
with further effects from integer rounding and composite calibration.

Every LP C/N-30 case trips the declared colour-risk proxy; no standard /
deviation combination has a sustained colour-safe usable threshold on this
sweep. Burst restoration by downstream chroma AGC is possible, but would
also amplify chroma noise and has not been tested. The filter sacrifices
D32-R2's strong-signal colour advantage. **Do not promote, automatically
switch to, or describe LP as an overall video-quality improvement from these
results.** It exists to make a reversible physical comparison possible.

## Verification and physical acceptance

Host tests:

- ESP-IDF **v6.0.2** ESP32-C5 parser/assembler accepts all eight bundles;
  binary is 2348 bytes with the same production loader geometry.
- Emulator equals an independent integer demod/IIR reference, all 65,536
  raw pairs, random multi-ring streams, PAL/NTSC raw and clean samples,
  every constant DAC value 0..63, and randomized six-bit OUTPUT remappings.
- Instruction traces compare every pre-filter worker LUT result (DAC,
  next phase, token bits) against stock. Legacy-program-plus-D32-LUT is a
  negative regression: it fails exact IIR because of the address carry.
- Stock generator/header/program checks and existing P64/U50/U85Q tests
  remain active. Compiled production selection, loader, restart coordinator,
  instruction/LUT readback and rollback code runs with mocked peripherals;
  errors injected at every restart stage in both directions; persistence,
  flight-log IDs, guard, menu path and fault latch are checked.
- CI assembles the new source, runs these tests and builds all existing
  firmware configurations. A successful firmware build still does not
  establish physical timing, sustained throughput or goggle acceptance.

Commands (numpy/scipy/numba installed; ESP-IDF 6.0.2 tools available):

```sh
python research/d32_lp.py generate
IDF_PATH=/path/to/esp-idf python test/test_d32_lp.py
IDF_PATH=/path/to/esp-idf python test/test_d32_lp_runtime.py
OPENBLAS_NUM_THREADS=1 D32_JOBS=4 python research/d32_lp_study.py research/data/d32_lp.json
python research/d32_lp_report.py
idf.py build
```

Hardware checklist (all still pending):

- [ ] Compare scope/logic captures of DAC bits: six-bit wiring, byte repeat,
  40 MHz TX and unique DAC20, both standards; check startup/reset transient.
- [ ] Stress ring wraps and prolonged video; RX overflow, TX empty, DMA fault,
  BitScrambler overload stay zero, without claiming gaplessness from counters.
- [ ] Compare identical-attenuation NTSC/PAL A/B/A at every deviation; sync,
  line rolling, burst amplitude/phase, actual colour killer and perceived noise.
- [ ] Compare strong-signal colour bars, fine luma/chroma detail and resistor
  DAC levels; no compensation should hide saturation, bias or lost colour.
- [ ] Exercise console/menu selection, shared cooldown, reboot persistence,
  OUTPUT settings and verified recovery; confirm firmware/log decoder identity.
- [ ] Record board, firmware SHA, VTX, goggles, attenuation, scope captures
  and pass/fail. Keep experimental if colour/lock regressions remain.
