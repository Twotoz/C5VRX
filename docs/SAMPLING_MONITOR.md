# Sampling timing: observe first, adjust only on proved need

C5VRX by Twotoz and the C5VRX contributors. This audit covers root
`calibration.c`, `sphase_logic.h`, `dsp.h` and `transport.c`.

The operator requested automatic timing monitoring and adjustment when
needed. The existing live monitor already called a disruptive three-position
carrier scan, despite comments saying passive/startup-only. This included
`receiver_pause()`, several `rx_clock_slip(1)` pulses and 16-window batches.
At the configured 1-ms tick, three successful batches alone take at least
96 ms; pause, two 20-ms waits and another verification batch take the
minimum toward 188 ms. Failed copies and up to six settle attempts extend
it. This is not an extremely fast one-step adjustment.

The live call now remains passive. Healthy first appearance of a carrier
does not trigger a scan. An excursion that would previously have scanned
reports `automatic_adjustment=deferred reason=live_actuator_unverified`.
Startup/manual sampling qualification remains available. The extracted
production-source test `test_sphase_runtime.py` verifies healthy/noisy
carriers, no-carrier, busy ownership and copy failures never scan, pause
or slip during live monitoring. Automatic adjustment is **not completed**
by this deferral; it identifies the missing actuator proof explicitly.

## Why the present clock pulse is not the required actuator

RX uses PLL_F240M /6. The existing wrapper temporarily changes the divider
to /7 for nominally 1 us, masks interrupts, then restores /6. At ideal rates
that interval produces roughly 34.3 reads instead of 40, a deficit of about
5.7 samples. Exact edge placement depends on clock-counter and register-write
timing. Returning the register to /6 restores rate, not the original phase,
sample history or producer/consumer separation. Repeating two pulses is
not a proved rollback to the original position. No such rollback evidence
is inferred from startup scans that happened to find a cleaner reading.

The pinned PCR header exposes an integer RX divider and enable/source fields;
it does not expose a documented fractional phase selector. A candidate
one-cycle /7 excursion could lengthen one RX period by one 240-MHz tick
(4.17 ns), but the current CPU register pulse does not prove exactly one
extended cycle. Clock gating/reset or changing sampling edge is likewise
not accepted without showing its exact resulting data sequence and phase.
The MODEM_DIAG period is three PLL ticks; shifting by three ticks changes
which bus word is sampled but can leave the same transition alignment.

Before automatic actuation, verify the actual received sample sequence,
phase step, restored rate and resulting RX/TX lag, plus a deterministic
return to the original phase. Invalidate old DMA-progress/keeper work across
that sampling epoch. The 40-MS/s hardware stream remains the architecture;
no CPU resampling or per-pixel pacing is proposed.

## Evidence that distinguishes timing from RF/DC/noise

Fourth-difference excess can rise from mixed transition bits, but also
from RF noise, clipping, picture bandwidth and deviation. Whole-picture
excess above 150 is a suspicion, not independent timing proof. Near a fixed
quantizer point, there is no changing waveform from which to measure phase.
Do not treat a near-zero excess there as verified timing, or move the sampler
to search for a signal that the analog/DC path has removed.

Use original received porch samples confirmed by real sync timing. Stable
porch frequency removes picture/deviation bandwidth from the comparison.
Require valid DC, usable code span, low clipping, non-repeated samples,
fresh matched RF/gain/filter/DC/transport context and a same-context
reference that showed genuine phase contrast. A saved best/worst from
another gain/TX/DC context does not supply that reference. Noise-only,
quantizer-floor and ambiguous observations are explicitly unobservable.

Collect short matched batches, not the existing 16-window 32-ms sequence
across periodic native acquisitions. The native mode opens every 20 ms;
each opening invalidates relevant measurements. A short stable window can
fit between openings, or one bounded transaction can hold the analog lease
so acquisition/gain updates defer. Do not call `receiver_pause()/resume()`
merely for a short live phase transaction: resume changes control context
and currently disables native mode on its next tick. Exclusive raw ownership
must separately cover the brief clock action and revalidation.

## Bounded policy implementation

`main/sphase_monitor.h` provides a pure necessary-only policy, tested by
`test/test_sphase_monitor.c`. Two distinct, fresh, same-context excursions
within 4 ms request at most one candidate position. It requires both
explicit actuator and original-phase rollback proof; without them it
stays blocked and performs no physical action. The current /7-for-1-us
wrapper must be passed as **unproved**.

After a proved move, the candidate must produce original, context-matched
porch evidence within the 4-ms transaction deadline, below the live limit
and better than baseline by more than twice the carrier measurement margin.
Otherwise the policy requests the proved original phase, then backs off
for 60 s. A failed restore or externally changed transport context stops
automatic adjustment; it never restores an old phase into a new pipeline.
These constants bound the policy, not yet the C5 hardware's execution time.

This helper is not wired to register writes. Remaining delivery is the
tagged porch collector, deterministic actuator/rollback, short ownership
and sampling-generation integration, followed by real timing/sequence tests
under native/gain/USB load. Host state-machine tests cannot prove a safe
phase step or a range improvement.
