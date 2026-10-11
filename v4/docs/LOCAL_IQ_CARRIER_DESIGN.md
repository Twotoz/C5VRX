# IQ-clock-assisted carrier acquisition: research follow-up

Extends **C5VRX by Twotoz and the C5VRX contributors**:
https://github.com/Twotoz/C5VRX and https://twotoz.github.io/C5VRX/.
The official website provides the Discord invite. GPL-3.0-only applies.
Base PR190 `7491a66`; publication authorized by the operator on 2026-10-11.
These are local simulation results, not firmware changes or board acceptance.

## Why the carrier estimate needs a different model

During a stable sync tip or burst-free porch, the FM signal approximates a
single complex tone. Averaging products before taking their angle reduces
near-origin phase clicks, but correlated receiver noise adds a predominantly
real covariance term. Thus even an accurate H clock does not make an adjacent
product's angle an unbiased estimate of sync or porch frequency.

[Frequency Estimation from Compressed Measurements of a Sinusoid in
Moving-Average Colored Noise](https://www.mdpi.com/2079-9292/10/15/1852)
studies sparse measurements and correlation-based frequency estimation with
colored noise. This supports the estimator family, not FPV video decoding or
our C5 implementation. Constant-tone covariance fitting is established work.

`local_iq_carrier.py` compares three received-only estimators: adjacent-product
angle, a constant-tone fit to covariance lags2..8, and a joint nonnegative
signal/noise fit to lags0..8. The last uses

`C(l) = S * exp(j*2*pi*f*l/40MHz) + N * rho(l)`.

The frequency grid is -10..+10MHz in10kHz steps. S and N are inferred, not
supplied true powers or CNR. `rho` is derived from the simulator's declared
5th-order10MHz Butterworth channel at80MHz, then decimated to40MHz. Its match
to a real C5 and the configured narrow filter is unverified. This assumption
must remain explicit; ADC folding and plateau transients also violate the
ideal tone model. The long-lag-only alternative needs no supplied noise shape.

The initial seed8100190 development estimates select sample locations from
the same4ms window used to acquire the clock. This is retrospective offline
analysis and cannot be called a3KiB streaming C5 implementation. It exposed
large adjacent-angle bias and catastrophic estimates at incorrect H phases.

## Causal fresh-window validation and its failures

Seed8200190 freezes that initial decision rule, acquires H timing on the first
4ms, then measures plateau statistics on the **next**4ms. The phase hypothesis
is translated into that new window without truth fitting or a new acquisition.
Both stages read received quantized IQ only. Nominal periods, sync/porch windows
and the frequency grid are design priors. Labels are evaluated afterwards.

All16 fresh off-centre echo/fade stress cases are admitted. Mean absolute
porch-frequency error falls from804.3kHz (adjacent angle) to106.7kHz (colored
covariance fit), with worst error257.1kHz. However, one of16 pure-noise controls
is admitted. A noise-dominated fit can have a small normalized residual while
its tiny inferred tone has an essentially arbitrary frequency. This version
is rejected as a basis for automatic correction. Its evidence stays frozen in
`iq-carrier-causal-validation`.

After that failure, the decision additionally requires both plateau long-lag
tone-fit residuals below0.15. Existing gates require a declared clock, at least
40 sync/20 porch sparse sample positions, colored-fit residuals below0.1 and a
porch-minus-sync separation between0.5 and3.5MHz. These rules are selected on
development data, not claimed as universal video/VTX validity requirements.

Independent seed8300190 tests this revised rule on16 new stress cases, six
other cases and64 new noise controls. All16 stress cases are admitted. Mean
porch-frequency errors are792.5kHz for adjacent angle,114.8kHz for long-lag-only
and115.4kHz for colored covariance; worst colored error307.1kHz. The expensive
colored-noise model does **not** improve average accuracy over the simpler
long-lag model in this confirmation. Three noise controls declare an H clock,
but none pass the combined plateau gate. Zero of64 is not a rare-false-alarm
guarantee and does not cover interference, DC or a different physical filter.

Crucially, the broad controls still contain an admitted4dB PAL case with1.304us
timing error and700.6kHz porch-frequency error. A coherent plateau fit is not
sufficient to establish the right H start. Automatic RF correction remains
unapproved by the evidence. This motivates testing frequency consistency across
the leading and trailing interior of the full4.7us sync pulse, plus separate
front/back porch evidence, using new data and independent validation.

## Does correct centring help the existing video path at all?

`local_iq_recenter_bound.py` answers this with a deliberately **oracle** bound.
Seed8400190 uses eight new full-field PAL/NTSC cases,6dB CNR, deviation1.2,
off-centre carrier-1MHz, echo and fade. It tests no correction, a known-direction
-1MHz correction and -1.5MHz. Frequency changes happen before the channel
filter in fresh waveform generation, rather than rotating already filtered IQ.
Matched seeds/scene parameters are retained. Goggle calibration uses the
inherited per-case clean oracle; it is not a blind real goggle evaluation.

| Ideal correction | Mean worse-of-two goggle H lock | Minimum H lock | Mean luma SINAD |
| --- | --- | --- | --- |
| 0 | 3.94% | 0% | 1.04dB |
| -1MHz | 94.75% | 78.83% | 0.46dB |
| -1.5MHz | 92.57% | 75.70% | -0.12dB |

Correct bounded centring can strongly improve goggle synchronization in this
stress class, but it is **not** a demonstrated improvement in active-video
fidelity. More correction is not necessarily better. This separates a useful
acquisition problem from the still-unsolved better-demodulator problem.

The1MHz case corresponds to the existing controller's four250kHz steps; the
1.5MHz case reaches the RF function's clamp and exceeds that four-step budget.
Neither case simulates a feasible controller trajectory, fresh observations
between writes,100ms settling, PHY discontinuities, gain changes or sticky
video lock. The controller and carrier estimator have not been connected.

## C5 implementation boundary

The clock accumulator already has standalone integer C and cross-compile
proof. This carrier estimator is still Python reference code. Algebraically,
two sets of nine complex int32 sums need144 bytes plus counts; they can be
accumulated on a subsequent window without a160KiB raw snapshot. That storage
calculation does not prove a streaming implementation, deadlines, overflow
bounds, missing-data handling or the numerical fit. The current Python uses
vectorized indexing and floating-point grid search.

Sparse plateau acquisition and slow scoring fit the intended low-rate control
architecture rather than CPU sample-rate demodulation. Physical timing under
the live ring, ownership, bus contention and preemption still need proof.
Current PHY writes suspend/restore the live path, are limited to+/-1.5MHz and
must respect settling and existing lock protection. No continuous free retune
or hidden RF correction is justified by these results.

## Reproduce

Use the environment/import setup in [the archive guide](LOCAL_RESEARCH_INDEX.md):

```powershell
python v4/tools/dsp_search/local_iq_carrier.py --seed 8300190 --repeats 8 --causal --nulls 64 --out replay-carrier-coherence
python v4/tools/dsp_search/local_iq_recenter_bound.py --seed 8400190 --repeats 4 --out replay-recenter-bound
```

Frozen executed sources and received-input hashes are retained under
`data/local_joint/iq-carrier-*` and `iq-recenter-oracle-bound`. The source in
each historical directory determines its historical rule; running the current
tool on an older seed does not reproduce the older decision logic automatically.
The research goal remains open: no globally best model, complete CVBS output,
color acceptance or physical range improvement has been demonstrated.

## Full-pulse consistency follow-up

Before seed8500190, freeze four additional regions: sync interior0.8..1.6us
and3.2..4.0us; separate front porch(-1.3..-0.5us) and back porch8.8..9.5us.
Each must have at least eight sampled positions and a long-lag tone-fit
residual below0.15. The two sync-frequency estimates must differ by less than
600kHz; the two porch estimates by less than800kHz. These are declared initial
engineering thresholds, not validated C5/VTX tolerances.

Independent seed8500190 admits all16 new stress cases and none of64 new noise
controls. Mean stress porch-frequency error is124.9kHz. Four ordinary/strong
control cases pass timing within500ns; one bad nominal PAL clock is rejected.
One admitted nominal NTSC clock still has0.620us median /0.665us p95 timing
error, although its porch-frequency error is only47.1kHz. Therefore frequency
acquisition admission and permission to synthesize H sync are different gates:
this result cannot justify pulse replacement aligned to that clock.

Replay seed8300190 with the new guard retains all16 stress admissions and
rejects the previously admitted4dB PAL case with1.304us clock error and700.6kHz
porch error. All22 replayed received-input hashes match the frozen original.
This replay verifies the known failure; it is not independent confirmation.
Results and executed sources are in `iq-carrier-pulse-validation` and
`iq-carrier-pulse-replay`. Earlier failures remain unchanged in their directories.

```powershell
python v4/tools/dsp_search/local_iq_carrier.py --seed 8500190 --repeats 8 --causal --nulls 64 --full-pulse --out replay-full-pulse
```

Six plateau covariance sets now require432 bytes of complex int32 sums plus
counts, instead of the earlier two-set144-byte estimate. No corresponding
integer C carrier kernel or scoring deadline has been demonstrated. Broad
low-CNR, narrow-filter mismatch, clock drift and structured interference remain
open, as does the closed-loop settled retuning trajectory. The stronger guard
is a research candidate, not a promoted automatic RF or CVBS feature.
