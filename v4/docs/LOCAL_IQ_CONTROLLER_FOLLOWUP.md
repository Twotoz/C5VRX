# Acquisition controller and additional demodulation failures

This extends C5VRX by Twotoz and the C5VRX contributors, based on PR190
`7491a660e26722afbb9dd9e24e69ba381b4f130d`. Canonical source:
https://github.com/Twotoz/C5VRX; official website and Discord invite:
https://twotoz.github.io/C5VRX/. Preserve GPL-3.0-only and source notices.
These are offline research artifacts, not firmware changes or C5 acceptance.

## Received-only automatic correction: large narrow benefit, failed controls

`local_iq_acquisition_loop.py` combines the sparse H observer and full-pulse
carrier gate. Each observation uses 4ms of received IQ for the clock and a
fresh following 4ms for sync/porch statistics. Source CFO, CNR, deviation and
clean video are unavailable to the decision; clean calibration enters only
the final goggle evaluation. Every RF change requires fresh clock acquisition.

The hypothetical controller retains steps no larger than 250kHz, at most four
writes, the +/-1.5MHz clamp, and a scheduled 100ms delay after each write.
It replaces the acquisition decision rather than reproducing the current AFC:
the existing 16 Phase8 observations, MAD, burst and sticky TRACK requirements
are absent. Settled independent snapshots do not model PHY transients, physical
AGC, continuous timing, DMA ownership, camera drift or CPU deadlines.

The first development run, seed 8600190, contains four target stress cases and
six controls. Target mean goggle H-lock rises from 0% to only 36.97%; two cases
receive no correction. Inspection found valid-looking plateaus rejected by
the full-pulse residual gate with small subgroup sample counts. This suggested
retrying fresh observations without weakening the gate.

The frozen three-retry policy is evaluated with fresh seed 8700190: eight
target cases and six controls, each a full field. The target class is 6dB CNR,
deviation 1.2, -1MHz source CFO, echo and fading, split across PAL and NTSC.
Use `min(h_ok, peak_h_ok)` for goggle H-lock. Final corrected and uncorrected
cases share the final noise seed and scene; this is not a favorable comparison
of initial noise against later noise.

| Result | Uncorrected | Automatic correction |
| --- | ---: | ---: |
| Eight target cases, mean H-lock | 0% | 94.15% |
| Eight target cases, worst H-lock | 0% | 81.47% |
| Target mean luma SINAD | 1.028dB | 0.625dB |
| PAL control seed 8700594, H-lock | 94.92% | 86.29% |
| NTSC control seed 8700611, H-lock | 92.02% | 50.97% |

The controller fails the overall benefit gate. Restoring sync does not prove
better active video; even the target mean luma SINAD decreases. The nominal
control regressions require a received-only way to protect an already useful
feed. No controller promotion, measured sensitivity gain or range gain follows.
Three retries are a development choice followed by fresh validation, not proof
of an optimal number or a rare false-correction bound.

## Imaginary covariance: conditional noise cancellation, inconsistent benefit

For proper stationary noise passed through identical real I/Q filters, its
expected lag covariance is real. For a tone, therefore,
`Im(C(l)) = S sin(omega*l)` without the real noise-covariance term. The
`local_iq_noise_free.py` reference fits that imaginary sequence. Eight exact
population checks with different real noise shapes verify the stated algebra.
They do not establish the assumptions for the C5, folded ADC lanes, unequal
I/Q filters, DC error or a finite nonstationary CVBS plateau. The two-lag ratio
identity is also recorded; it is ill-conditioned near zero imaginary covariance.

A replay of the first 16 stress cases in seed 8300190 gives sync MAE 70.63kHz
versus 64.38kHz for the existing long-lag estimator; porch MAE is 105.09kHz
versus 114.82kHz. This is a mixed ablation on frozen statistics, not independent
validation or a consistently better estimator.

An independent paired filter experiment, seed 8800190, contains four scenes
at each of two filter settings. An extra 15.359MHz simulator filter deliberately
mismatches the fixed noise model. Global RMS normalization is simulated, not
physical AGC; neither setting identifies the physical C5 receive filter.

| Setting / plateau | Imaginary-fit MAE | Long-lag MAE | Fixed-noise-fit MAE |
| --- | ---: | ---: | ---: |
| Original filter, sync | 15.00kHz | 35.00kHz | 32.50kHz |
| Original filter, porch | 118.93kHz | 41.43kHz | 43.93kHz |
| Extra filter, sync | 47.50kHz | 30.00kHz | 30.00kHz |
| Extra filter, porch | 101.07kHz | 94.64kHz | 89.64kHz |

The alternative has no consistent benefit and is not selected. Algebraic
independence from a noise model does not remove finite-sample variance.

## More instruction-path context: legal source, no quality winner

`local_pc_confidence.py` reuses the previously proved eight-slot instruction
schedule. The previous published DAC most-significant bit selects one of two
IQ encoder banks with different near-origin radii. This changes received-signal
quantization without extra sample-rate CPU processing. Each bank still uses
28 angular tokens and four quadrant tokens; the decoder preserves the original
gains. All six closed-form radius pairs retain two bundles per 50ns, a shared
2KiB LUT and eight instructions. The original-radius control is byte-exact U85Q.

All six programs pass the official ESP-IDF v6.0.2 C5 assembler and independent
source dataflow checks on 8192-byte streams. This proves neither live bus timing
nor a quality benefit. The fresh seed 8900190 full-field study has 126 paired
rows across 14 scenarios, including offset and I/Q errors, and historical
HC50/RANGE32 controls. None of the five changed-radius candidates passes the
benefit gate. For example radius pair (1.6,0.8) decreases the weak-picture
aggregate from 0.97481 to 0.95830 despite a small mean luma SINAD increase
from 1.380dB to 1.444dB. Larger radii degrade much more. Preserve the rejected
programs rather than presenting extra state as an automatic improvement.

## Architecture history and the remaining question

Changing the number of bundles per unique DAC value is already C5VRX research.
`v4/docs/RESEARCH.md` describes the three-bundle 75ns route. The legacy
`c5vrx2_wbfm_direct6_4to1.bsasm` already accumulates four adjacent differences
with a counter, on an older packed input. It is not a new independent discovery
or directly usable proof for the current eight-bit 40MS/s input. PR68's measured
four-bundle-per-50ns FIFO failure must not be confused with four bundles spread
over 100ns. A different rate still needs a complete stream/dataflow proof,
bandwidth and phase-wrap analysis, and physical timing acceptance.

The open question is how to preserve useful active-video information and avoid
harmful interventions while recovering sync, within the actual C5 budget.
No tested candidate establishes a global optimum or the best possible model.

## Reproduction

Use the environment and import setup in LOCAL_RESEARCH_INDEX.md. Write into
new replay directories rather than overwriting frozen evidence.

```powershell
python v4/tools/dsp_search/local_iq_acquisition_loop.py --seed 8700190 --repeats 4 --retries 3 --out replay-acquisition-retry
python v4/tools/dsp_search/local_iq_noise_free.py --out replay-imaginary
python v4/tools/dsp_search/local_iq_noise_free.py --channel --seed 8800190 --repeats 2 --out replay-filter-mismatch
python v4/tools/dsp_search/local_joint_study.py --pc-confidence --full --seed 8900190 --workers 2 --out replay-pc-confidence
```

The six archive directories are listed in LOCAL_RESEARCH_INDEX.md. Source
snapshots, seeds and result hashes retain both positive and negative evidence.
