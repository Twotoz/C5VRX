# Local C5 demod research

This extends C5VRX by Twotoz and the C5VRX contributors:
https://github.com/Twotoz/C5VRX and https://twotoz.github.io/C5VRX/.
The official website provides the Discord invite. The original GPL-3.0-only
license and existing research provenance apply.

Base: PR190 head 7491a660e26722afbb9dd9e24e69ba381b4f130d.
The experiments were authorized as local code and simulations, initially
without branch/PR pushes. On 2026-10-11 the operator explicitly requested
committing all research to a new PR. Publication is now authorized; board
flashing, default changes and firmware promotion remain outside this work.
See [the research index and reproduction guide](LOCAL_RESEARCH_INDEX.md).

The active research objective is to find a materially new, C5-executable
IQ-to-CVBS approach with independent evidence of improvement over U85Q and
historical controls. Novelty alone and promising simulations do not establish
a working physical receiver or revolution. Preserve failures and limitations.

First ablation: conditional use of sample B, 24-angle/8-coarse confidence
tokens, nearest-quadrant interval correction, nonlinear origin innovation and
an unrounded-output diagnostic. Existing untrained search, HC50 geometry,
belief ROMs and counter experiments are prior work, not independent inventions.

All candidates retain the existing 1024x16 shared ROM, eight slots, two
bundles/50ns, raw RX40/raw32K, TX-only duplicate DAC6 at physical40M.
Host assembly emulation is dataflow evidence only, not physical throughput.
The initial short screen has only 64 nominal lines, excludes long dropout
recovery and cannot establish field continuity. Full-field confirmation and
physical acceptance remain necessary.

The inherited goggle measurement calibrates each candidate on clean truth for
each case. That is an oracle surrogate, not demonstrated on-device AutoFit.
Keep this visible; a large H-lock percentage is not colour/goggle acceptance.

## Completed formula and counter confirmation

`data/local_joint/confirmation` contains 112 paired rows: 12 formulas plus
HC50/RANGE32, eight full PAL/NTSC cases, seed6300190. No formula gives a
meaningful combined weak/strong improvement. Conditional B weight .5 is
exactly baseline; weight1 worsens weak picture. Confidence compression loses
0.291dB strong SINAD, and its fixed carrier prior collapses weak picture to
0.833 versus U85Q's0.973. Nonlinear origin correction improves weak SINAD by
0.139–0.189dB but lowers weak picture to0.929–0.908. The unrounded-output
diagnostic loses0.315dB strong SINAD. Short-screen improvements did not survive
fresh full-field confirmation. This is evidence against these candidates,
not a theorem of global C5 optimality.

`counter-corrected` contains56 full-field rows, seed6500190. The main
counter-assisted loop has identical weak picture0.9624, about+0.010dB weak
SINAD, and -0.808dB strong SINAD. Lower origin gain and other output laws
regress further. `counter-confirmation/INVALID.md` marks the earlier run
invalid: its host model assumed encoder bank3, but the independent source
emulator showed bank0 and the full raw-byte addition into the counter.
Preserve that failure; never cite the invalid run as evidence of performance.

Early runner source hashes were recorded without source snapshots; subsequent
runner extensions mean those hashes cannot be reproduced from the current
runner file alone. Their explicit frozen LUTs and scenarios remain available.
Starting with circular-confirmation, executable research sources are archived
with their corresponding protocol. Dependencies remain pinned to7491a66.

## New circular and constrained models, 2026-10-11

The six closed-form candidates in `local_circular_models.py` use unchanged
U85Q tokens and hardware scheduling. They test:

* Circular vector averaging rather than linear wrapped-angle averaging.
* A Cauchy-like innovation gain, applied to ordinary and origin observations.
* Consistent phase-state/output frequency limits: constrain the internal
  phase advance as well as the DAC to the representable frequency range.
* Declared fixed1MHz predictive rotation for the first and third families;
  this is not runtime knowledge of the test carrier or instantaneous video.

These are new local experiments; circular estimation, robust PLLs and FM
threshold extension are established research, not inventions claimed here.
The [phase-jump correction patent](https://patents.google.com/patent/EP0940958A1/en)
uses samples before and after a potential jump and exploits low-bandwidth
stereo modulation. It does not establish that the same correction fits the
C5's causal two-lookup path or preserves wideband CVBS colour/detail.

Independent source emulation passes six8192-byte random streams. Full-field
confirmation uses14 cases,126 rows, seed6700190, PAL/NTSC, including negative
and positive carrier offsets, DC, IQ gain/phase error, fade and multipath.
Results in `circular-confirmation` still use the clean calibration surrogate.

U85Q weak picture is0.987141, weak SINAD1.39079dB, strong SINAD13.90295dB.
Zero-prior circular averaging gives weak picture0.987176 (a negligible
0.00345 percentage-point change), weak SINAD1.15084dB and strong
SINAD13.71158dB. The other candidates regress substantially. Consistent
state/output limiting reduces weak picture to0.8902, despite virtually
identical strong SINAD. A physical-looking consistency constraint therefore
does not imply better estimation under noisy observations.

The gain-reduction models have a structural risk: if the maximum permitted
phase correction is below the instantaneous FM advance, the carrier outruns
the loop. The tested Cauchy-like law gives .85*e/(1+a*(1-cos(e))). Its maximum
ordinary correction restricts tracking even though the DAC still spans
-6..9MHz. Noise rejection and tracking capacity must be checked together.
No new candidate is promoted or claimed better than U85Q.

## Hierarchical timing route: logical proof and unresolved transport

`local_sync_words.py` finds a two-token word [0,0] which brings all32 U85Q
states to one state in100ns. A272-byte precomputed IQ template then emits
exact DAC4 for4.7us and DAC25 for blanking. Independent assembly emulation
checks every initial state. The decoder has one input-pair publication delay;
the template placement must compensate50ns. No physical DAC voltage, timing
acquisition, field continuity, colour or goggle benefit is proved.

Synthetic IQ and descriptor splicing are historical C5VRX approaches; see
`../../docs/c5vrx3-menu-and-raster-architecture.md`. The reset word resolves
one inherited-state issue logically. It does not resolve unsafe descriptor
ownership or make the old menu splice safe. Current v4 live TX uses the
driver's cyclic raw ring; no live splice has been implemented here.

`local_descriptor_geometry.py` tests an immutable descriptor alternative,
conditionally assuming four-byte input alignment and nominal camera timing.
For PAL, raw-ring and H phases repeat after64 lines/163840 bytes; at least128
descriptors are needed. For NTSC, the40MHz input period is22880/9 bytes/line.
Rounding boundaries gives a nine-line2540/2544-byte schedule with at most
44.45ns nominal error, but its raw-ring phase repeats only after9216 lines.
At least18432 descriptors are required (221184 bytes if a descriptor is12
bytes), before ring splits, field templates and normal firmware allocations.
Actual ABI size is not established by this conditional calculation. Thus an
immutable chain is potentially small for nominal PAL H timing but costly for
NTSC, and neither handles camera clock drift or newly acquired line phase.

Remaining work includes a received-IQ hierarchical timing model, full H/V
recovery and its CPU deadline, and a transport ownership design supported by
actual peripheral behavior. More valid sync would improve display continuity;
it would not by itself prove recovery of additional active-video information.

## Explicit frequency memory and instruction-path memory

`local_velocity_models.py` allocates either32 phase bins x2 frequency modes
with16 observation tokens, or16 phase bins x2 modes with32 tokens. Frequency
centres are fixed -1/+4MHz; an innovation estimate selects the next mode, with
zero or0.75MHz hysteresis. This is a closed-form coarse second-order tracker,
not a learned model or access to actual frequency/CNR. Four sources pass host
assembly emulation on8192-byte streams. `velocity-confirmation` has98 rows on
14 full-field scenarios, seed6900190. Every variant regresses: weak picture
0.774–0.861 against U85Q0.949; strong SINAD11.25–11.82 against13.90dB. The
loss of observation or phase resolution outweighs this frequency memory.

This led to a distinct resource experiment: store a binary mode in the
instruction path instead of the LUT state. `local_pc_prediction.py` retains
all32 phase states and32 tokens. Four controller/worker pairs occupy exactly
eight slots, but conditional worker branches select one of two encoder banks.
The controller publishes the previous DAC, the worker branches on its MSB;
the next controller therefore uses frequency-band information delayed by one
additional output pair. Branch targets are always controllers. All paths
consume16 input bits and emit16 output bits every two bundles, in the host
model; no counter, CPU sample loop, raw-ring write or third lookup is added.

Each encoder bank uses full A IQ8 plus two literal bank bits. B is discarded,
as it already is by U85Q's zero-B-weight formula. The lower11 bits of each
shared word retain the unchanged U85Q state/DAC table; the upper5 bits encode
mode-dependent observations. This construction avoids taking a frequency bit
away from phase or observation resolution. It is not a claim of unlimited
memory: two contexts, a delayed DAC-based switch and only eight slots are
available in this proof.

Three sources pass the official Espressif ESP-IDF v6.0.2 ESP32-C5 assembler;
see `pc-source-proof/proof.json`, frozen .bsasm/.bsbin and source/target hashes.
The [official instruction documentation](https://docs.espressif.com/projects/esp-idf/en/latest/esp32c5/api-reference/peripherals/bitscrambler.html)
documents conditional branches and access to the preceding output register.
Source emulation proves alternating controller/worker paths and8192-byte
stream equivalence. With zero predictive rotation, this new schedule's DAC
stream is byte-exact U85Q, independently checked. Physical branch timing and
continuous board transport remain unvalidated.

The first use is predictive observation rotation by (1/k-1)*frequency*50ns.
This is only a local linear approximation to a predictive phase estimator;
wrap boundaries, coarse origin tokens and frequency switching break global
equivalence. `pc-confirmation` preserves84 full-field paired rows,14 scenarios,
seed7100190. Zero-scale mode matches U85Q exactly: weak picture0.958388 and
strong SINAD13.886807dB. Scale0.5 reduces weak picture to0.928046 and strong
SINAD to13.537368dB; scale1 reduces them to0.699274 and12.456889dB. Thus the
extra context is source-feasible but this first predictor is rejected. Extra
memory alone is not proof that an estimator can exploit it beneficially.

One further timing alternative removes the huge nominal NTSC descriptor
repeat: a22880-byte ring equals nine NTSC lines; a30720-byte ring equals12
PAL lines. Conditional H-only descriptor lower bounds fall to18 and24.
These are changes to the current exactly32768-byte invariant and require a
separate firmware transport design and validation. No firmware invariant was
changed. Smaller buffers also shorten the producer/consumer lead (half-ring
286us NTSC or384us PAL); camera drift, phase acquisition, V timing, transport
ownership and physical acceptance are still unresolved.

## Complex-product schedule rejection and direct-IQ timing evidence

`local_complex_counter.py` attempts512 product entries +512 angle entries,
two arithmetic counters and cyclic1/2/4-product windows in eight slots. It
passes host dataflow checks but the official C5 assembler rejects every
source: LUT16 and B-counter routing overlap in the worker. The failure,
sources and assembler hashes remain in `complex-source-proof`. The module
is explicitly marked rejected. No signal-quality results are promoted from
that physically invalid schedule.

The CPU-side sparse coherent-IQ timing reference then provides a positive,
limited result:16 fresh echo/fade/off-centre PAL/NTSC simulations retain a
usable H clock while U85Q's two goggle models average0.953% H lock. This is
received-only acquisition with held-out timing labels, not repair or picture
acceptance. General low-CNR and some declared-lock timing failures persist.
See [the concrete design and full evidence boundaries](LOCAL_IQ_CLOCK_DESIGN.md).
