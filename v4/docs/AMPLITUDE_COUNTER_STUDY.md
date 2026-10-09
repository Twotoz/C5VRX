# High-amplitude failure and counter-history research, 2026-10-09

## Result and firmware decision

The operator reports that Ω gives ugly video and fails at high amplitude. This
follow-up started from PR190 head `1604de3c34ba392bd3cb16026eec03ad2206628d`.
There is no new board IQ capture or measured input amplitude available, so the
synthetic failure reproduced below is consistent with that report, not a
measurement of its exact physical cause.

**Ω is quarantined. No new all-range replacement qualified.** Value13 is kept
for reproducibility but is no longer selectable: saved13 restores RANGE32 and
Y skips it. Existing PAIR, EDGE, Fusion and default settings are unchanged.
The new arithmetic/history schedules are genuine shared-LUT executable source
experiments, but require a different TX clock profile and fail the weak-sync
acceptance gate. They are not exposed as firmware modes. This is a firmware
regression correction plus preserved research, not a claim to have solved the
original maximum-range mission. PR190 must remain unmerged.

## Reproducing the amplitude failure

`amplitude_cases.py` reconstructs the exact complex signal/noise used by the
existing generator, asserts byte equality at nominal RMS3, then requantizes
those same traces. RMS means synthetic amplitude in lane-cell units, not volts,
RF gain register values or measured board amplitude. Nominal voltage calibration
is held fixed; folded output is not recalibrated to conceal its distortion.
The comparison uses matched raw IQ hashes and the corrected latency scoring in
`omega_research.measure`. A fixed lag is determined on clean IQ, then frozen
for noisy IQ; no per-noise alignment search is allowed.

The initial 40-case PAL/NTSC sweep uses seed1310190, C/N4,8,20,30, amplitudes
1.5,3,4.8,7,10, and two independent standard/deviation cases per condition.
Strong C/N20/30 averages (four cases per amplitude):

| IQ RMS | EDGE+AutoFit SINAD / H+V misses | PAIR+AutoFit | published Ω |
|---:|---:|---:|---:|
| 1.5 | 3.64 / 0 | 8.40 / 0 | 4.21 / 0 |
| 3 | 3.65 / 0 | 10.57 / 0 | 4.70 / 2 |
| 4.8 | 3.25 / 0 | 10.97 / 0 | 4.55 / 0 |
| 7 | 3.60 / 0 | 11.12 / 0 | 2.40 / 43 |
| 10 | 3.37 / 17 | 3.76 / 6 | 1.30 / 36 |

SINAD is dB. At RMS7 Ω fails while PAIR still works: the Ω problem cannot be
explained solely by ADC folding. At RMS10 the actual sign-preserving ultrafine
lane model folds sufficiently to damage the controls too. The published Ω
teacher assumed fixed RMS3 and the student discards raw amplitude/phase detail
into eight tokens. Neither its training evidence nor a good nominal score
established amplitude generalization. This does not prove which internal Ω
state first fails on the operator's board.

## A different legal information path

The original free-form 128-state/eight-token Ω compression did not recover the
frontier. We investigated actual BitScrambler counter arithmetic instead of
adding another PLL constant or external EDGE/PAIR controller.

The ESP32-C5 has one shared physical LUT16, 1024 sixteen-bit words (2 KiB).
There are still only two lookup instructions in eight slots per 50 ns. The
counter inputs can coexist with LUT16 inputs in a bundle. LUT32 overlaps the
counter operand inputs: combining them is rejected by the actual Espressif
assembler, even when a permissive software model suggests otherwise. No second
LUT is assumed. Operand bits24..25 select lookup-bank bits as well as arithmetic
operands; all generated sources explicitly account for that coupling.

All prototypes split the single table into encoder banks1/3 and scalar banks0/2
(256 words per bank). `write8`, instead of duplicate `write16`, frees output
register bits8..15 to retain encoded phase. Counter A independently retains
previous phase and, in the history designs, previous frequency information.
There is no raw-rate CPU work or raster generator.

**Important clock limit:** these sources consume two RX bytes per one TX byte.
They need RX40/TX20, whereas current firmware uses RX40/TX40 and duplicated DAC
codes. Model cadence and assembler acceptance establish the dataflow, not the
actual FIFO, I2S clock configuration, setup margin or board voltage behavior.
Using these tables with the existing TX40 transport would be incorrect. No
transport clock change is shipped in this follow-up.

### Exact128 arithmetic reference

For an encoded phase `p` in 0..127, the first lookup emits

```
positive = 2*p + 1
negative = -positive - 1  (mod 256)
```

The counter adds the current positive phase to the saved negative phase. Its
high byte is `d = 2*(p_current-p_previous)-1 (mod 256)`. The scalar lookup removes
the bias and converts signed frequency to DAC6. This preserves 128 phase cells
without placing a phase/frequency grid in a shared word. It still uses only the
A endpoint of each IQ pair; B is discarded. A 64-phase context variation also
retains a coarser past difference. The independent CW test establishes frequency
polarity/scale; the waveform score uses the fixed pipeline lag, not a guessed
zero-delay output.

Exact128 reaches strong SINAD10.56,11.35,11.38 at RMS3,4.8,7 in the initial sweep,
comparable to PAIR, but its naked phase difference gives many weak FM errors.
This is an information-path discovery, not a robust demodulator by itself.

### Regime risk prototypes: a negative result

Six symmetric and eight asymmetric two-regime prototypes use empirical
likelihoods and a bit of scalar state to select a risk estimate. Their statistics
use high/low C/N labels offline only; the runtime inputs are raw IQ and counter
history. A symmetric .995 prior traps the initial diffuse state because one
observation cannot overcome its prior odds. Asymmetric priors improve startup
but fail the combined gates.

These are **regime proxies, not exact Bayesian filters**: the arithmetic residue
confounds an older sign-history bit at sign changes. The implementation now
states this explicitly. Their negative result cannot rule out a correctly
compressed Bayesian filter or establish an impossibility bound.

### Frequency-history risk transducer

With 32 learned phase cells and constant `K=80`, the encoder emits
`positive=8*p+1`, `negative=-8*p+K (mod 256)`. Counter-low retains the preceding
encoded difference. Two selected bits of that low byte provide history classes
`h1,h0`; the addition also produces carry `b`. The scalar address is exactly

```
d = 8*delta_p + K + 1 + 2*h1 + 4*h0 + b  (mod 256)
b = 1 - (d & 1)
base = d - b - 1
history = (base >> 1) & 3
phase_difference = signed(base - (base & 7) - K)
```

The decomposition is exhaustively tested for every signed phase step, history
class and carry. K shifts coarse-history boundaries approximately to -6.25,
-1.25,+3.75,+8.75 MHz (the small history/carry terms also shift them). Thus history
can distinguish plausible sync frequency from blanking without generating a
line clock or deciding solely on phase-step magnitude.

The phase encoder learns circular clean-carrier means from raw IQ. Scalar codes
minimize empirical squared video error conditional on the *actual hardware
address*. Frequency targets are causal clean phase increments ending at the
previous A endpoint; there is no future IQ in runtime. Six joint encoder/DAC
weight variants were screened on training data. Strong weights1,10,100 and
sync weights1,4 test the real noise/detail trade-off, not an arbitrary clipping
threshold. Counter transition semantics are structural; this is not unrestricted
optimization of all possible transducer policies. Affine calibration uses the
simulated deviation/centre as an AutoFit surrogate, supplied equally to controls;
it is not demonstrated on-device blind calibration for these prototypes.

### Endpoint-quality variant

Six further variants retain a quantization-cell angular-quality bit in encoder
bit1. Current endpoint quality selects scalar bank0/2; older endpoint quality
also perturbs the arithmetic residue. The scalar risk estimate conditions on
the actual resulting address and marginalizes the ambiguity. It must not be
interpreted as an unambiguous phase/history/quality tuple. Thresholds1.5,2.5,4
and strong weights10,100 were frozen before the final confirmation. This family
adds observation reliability rather than another phase-step limiter. It still
fails weak sync; removing false sync by also removing genuine sync is not success.

## Independent evidence and stopping decision

All raw results, models and generated shared tables are in
[`data/amplitude_followup`](data/amplitude_followup). There are 28 new candidates:
two analytic schedules, six initial regime proxies, eight asymmetric proxies,
six history risk models and six endpoint-quality models. Training data seeds
1410190,1411190,1710190,2010190 are separate from confirmation seeds.

There are **224 independent follow-up stimulus scenarios**, counting the shared
seed1310190 sweep once: four 40-case sweeps at1310190,1510190,1610190,1810190,
and two 32-case final confirmations at1910190,2110190. Each model in a comparison
receives the same IQ. The later two tests use all four unseen content indices
(zoneplate, checker, texture, OSD), PAL/NTSC, two deviations (.69 and1), and IQ
RMS3/7. Scenarios from different model sweeps are not pooled into a misleading
winning score. Selection uses training only; failed models are saved too.

Frozen history-model confirmation at seed1910190, eight cases per C/N:

| C/N dB | EDGE SINAD / misses / detail | PAIR | history counter | Exact128 |
|---:|---:|---:|---:|---:|
| 2 | 3.82 / 2 / .377 | .63 / 45 / .453 | .85 / 80 / .435 | -.14 / 86 / .369 |
| 6 | 5.13 / 0 / .499 | 4.56 / 7 / .657 | 3.84 / 58 / .627 | 3.78 / 74 / .588 |
| 13 | 5.18 / 0 / .519 | 8.28 / 0 / .843 | 8.07 / 2 / .816 | 7.83 / 10 / .814 |
| 16 | 5.07 / 0 / .527 | 9.37 / 0 / .890 | 9.47 / 0 / .871 | 9.11 / 1 / .875 |

The weak failure also occurs at RMS3 alone: at C/N2 EDGE misses1, PAIR19,
history39; at C/N6 EDGE0, PAIR1, history26. It is not just overload at RMS7.
The earlier seed1810190 history screen beats PAIR strong SINAD at RMS3/4.8/7
(11.43/11.71/11.72 vs10.60/10.99/11.13), yet its recovered-detail score is
slightly lower, and at RMS1.5 it misses ten strong pulses. Higher SINAD therefore
does not establish superior recognizable moving video.

Final endpoint-quality confirmation at seed2110190, eight cases per C/N:

| C/N dB | EDGE SINAD / misses | PAIR | endpoint quality | Exact128 |
|---:|---:|---:|---:|---:|
| 2 | 3.91 / 6 | .66 / 37 | .32 / 88 | -.08 / 88 |
| 6 | 5.10 / 0 | 4.39 / 6 | 3.44 / 64 | 3.79 / 72 |
| 13 | 5.22 / 0 | 8.22 / 0 | 7.45 / 3 | 7.78 / 3 |
| 30 | 4.76 / 0 | 11.22 / 0 | 11.21 / 0 | 11.74 / 0 |

At C/N30 endpoint-detail .945 vsPAIR .950 again provides no detail win. At C/N2
its low false-sync score .011 accompanies88 missed genuine pulses: this is a
failed sync decoder. False-sync, click and detail metrics remain in each JSON.
None of the constrained strong-gate screens qualified. Utility-selected models
are retained for diagnosis, explicitly not recommended winners.

The follow-up crops each test to32768 IQ samples (0.8192 ms); these are short
local-video and horizontal-sync tests. They do not establish full-field vertical
sync, long fades, RF range, gain-controller recovery or goggle acceptance. The
previous Ω full-field results remain in its original study and are not silently
attributed to these counter schedules. Fusion's two component decoders are
controls here; live switching/DMA continuity is not simulated anew.

This bounded search has demonstrated a useful counter dataflow but not a
frontier-crossing decoder. More small weight/prior sweeps are unjustified. The
observed constraints are loss of the B sample, 32-phase resolution when history
is retained, ambiguous history/quality residues, limited scalar-address capacity,
and incomplete amplitude/process inference. They are not a proof that all legal
schedules or finite-state Bayesian estimators must fail.

## Reproduction and validation

Use Python3.12 and the pinned `tools/dsp_search/omega_requirements.txt`. From v4:

```sh
export PYTHONPATH=tools:tools/dsp_search
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 C5VRX4_SEARCH_LANE=ultrafine
python tools/dsp_search/amplitude_audit.py --output /tmp/amplitude-baselines.json
python tools/dsp_search/counter_diff_audit.py --output /tmp/counter-reference
python tools/dsp_search/counter_bayes_search.py --output /tmp/counter-bayes
python tools/dsp_search/counter_bayes_refine.py --stats /tmp/counter-bayes/statistics.json --output /tmp/counter-refined
python tools/dsp_search/counter_history_search.py --output /tmp/counter-history
python tools/dsp_search/counter_final.py --model /tmp/counter-history/selected_model.json --output /tmp/counter-final.json
python tools/dsp_search/counter_quality_search.py --output /tmp/counter-quality
python tools/dsp_search/test_counter_history.py
python tools/verify.py
```

Use fresh output paths; scripts refuse to overwrite evidence. The four counter
tests passed, including70k-byte source/model equivalence and causality for five
schedules, continuous output cadence, exhaustive history decomposition, scalar
calibration invariance and independent CW frequency scale. `verify.py` passed
23 C regressions and source-driven DSP tests, including saved13 quarantine.

Five selected sources were also compiled with the **actual ESP-IDF6.0.2
ESP32-C5 assembler**, not only `bs_model.py`:

```sh
python "$IDF_PATH/tools/bsasm.py" \
  -c "$IDF_PATH/components/esp_driver_bitscrambler/bsasm_targets/esp32c5.json" \
  SOURCE.bsasm OUTPUT.bsbin
idf.py -C v4 -B build-c5 -DIDF_TARGET=esp32c5 build
```

The quarantine firmware compiled with ESP-IDF6.0.2 and Espressif GCC15.2.0;
app size1,304,288 bytes. This workspace disables the unused component manager
because its process probe cannot find the sandbox PID; there are no managed
component manifests. Compiler-helper execute permissions were repaired outside
the repository. Build/test logs, source and binary hashes are saved under
`data/amplitude_followup/validation`. Firmware build success concerns current
TX40 firmware, not execution of the research TX20 schedules.

Hardware references: [Espressif BitScrambler guide](https://docs.espressif.com/projects/esp-idf/en/latest/esp32c5/api-reference/peripherals/bitscrambler.html),
[ESP32-C5 TRM](https://documentation.espressif.com/esp32-c5_technical_reference_manual_en.pdf),
and the checked-out IDF6.0.2 `tools/bsasm.py` and
`components/esp_driver_bitscrambler/bsasm_targets/esp32c5.json`. Previous
mathematical literature/teacher derivation is in `OMEGA_BELIEF_STUDY.md`; this
follow-up does not claim a new globally optimal Bayesian solution.

## Board procedure and next useful investigation

1. Flash the quarantine build with NVS/PHY preserved. Verify saved13 falls back,
   Y omits Ω, and explicit PAIR/EDGE restore their prior behaviour.
2. With the same camera/VTX, compare PAIR and EDGE at strong RF and stepped
   attenuation. Use moving texture, OSD and chroma; record goggle output and
   current gain/DC/AutoFit diagnostics. Include at least two real VTX deviations.
3. Capture raw IQ around the reported high-amplitude corruption, matching a
   clean control. Keep actual gain and lane configuration. Distinguish folded
   cells, DC displacement, I/Q imbalance and ordinary noise before retuning.
4. Do not load these TX20 research programs into the TX40 firmware. A future
   stopped-engine TX20 bench integration must first verify FIFO pacing, DAC
   codes and voltages on a scope, then sustained ring-wrap and output timing.

The strongest next direction is a causal amplitude/DC-aware reference teacher
on real IQ, with a robust process model distinguishing valid fast video from
phase ambiguity, before distilling another compact state representation. The
new counter resource can then retain endpoint likelihood or multiple frequency
hypotheses with an explicit, proven address layout. Purely causal demodulation
cannot recreate indefinitely missing raster timing; no hidden line flywheel is
implemented. Until new independent and board evidence clears both gates,
PAIR remains the strong-detail control and EDGE the weak-sync control.
