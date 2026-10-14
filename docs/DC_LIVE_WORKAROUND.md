# DC without a quiet-channel sweep

C5VRX by Twotoz and the C5VRX contributors. This study extends the existing
carrier-circle estimator, physical ultrafine ADC model and U85Q-LP/kp table
research. Requested by the operator on 2026-10-14. It is host research, not
a measured RF range improvement. The carrier-evidence guard is integrated
in calibration collection; automatic token-centering remains host research.

The smallest promising workaround is to estimate an identifiable DC centre
from real live IQ, and subtract it in the hardware lookup that maps the IQ
endpoint to its phase token. Only the high five controller-token bits of the
existing 1024-entry LUT change; its lower eleven phase/DAC worker bits stay
identical. The continuously running two-bundle path gains no per-sample CPU
work. It needs no quiet RF input, per-gain DAC response search or another full
set of tables. The existing guarded, verified halted LUT-restart contract
still applies: updating a running LUT remains unsupported.

## A real failure in the current evidence gate

The four archived VTX-off IQ windows have only 5–6 distinct byte codes. The
actual production `dsp_circle_dc` nevertheless returns `circle=1` for all
four, with envelope ratios 1148, 9999, 2854 and 9999. A tiny quantizer cloud
can fit an apparently perfect circle. Circle fit plus envelope ratio alone
does not establish a real FM carrier or an identifiable circle centre.

`main/dc_live.h` adds these carrier-evidence gates:

- At least 1024 samples from the caller's unchanged gain/context.
- Minimum covariance eigenvalue at least 1 cell squared; each independent
  axis therefore contains measurable spread.
- Small/large covariance eigenvalue ratio at least 0.2, avoiding short arcs
  and nearly one-dimensional quantizer clouds.
- Existing circle fit, envelope ratio at least 300, and less than 20 per
  mille rail occupancy. These are research acceptance thresholds, not a
  universal calibrated carrier decision.

The helper rejects all four real VTX-off false circles, the current-board
noise-like capture and a deterministic short-arc fixture. It accepts four of
six archived weak-VTX windows; their true DC is unknown, so acceptance is not
proof of estimator accuracy. It also recovers a known shifted clean circle
within 0.1 cell. Original `dsp_circle_dc` and its noise/mean fallback remain
unchanged. Calibration `collect()` now calls `dc_live_measure()`: a rejected
circle returns the plain mean while preserving the envelope diagnostic, so
drift cannot relabel a rejected high-envelope cloud as ordinary receiver noise.

The first integrated firmware improvement uses qualified spatial spread
for carrier DC eligibility, while preserving the plain-mean result for valid
receiver-noise measurements. That avoids turning a quantizer-floor cloud into
a high-confidence carrier correction. A complete automatic token-centering
controller additionally needs freshness, settled-gain, filter/PHY context,
bounded center movement, context reset and verified restart/rollback behavior.

## Held-out comparison

`research/dc_live_workaround.py` uses seeds 111–114, alternating PAL/NTSC,
deviation/filter/kp combinations 0.69/10 MHz/0.60, 1.0/12 MHz/0.85 and
2.0/16 MHz/0.85, and C/N 2/10/26 dB. It applies DC before the physical ADC
and ultrafine folding, and processes 131072 samples per case. The actual C
estimator sees 32 distributed 256-byte probes, totalling 8192 samples. Clean
output calibration is shared by all compared methods within each scene.
These fixed-context replay windows are not a real-time streaming controller.

There are 108 synthetic scenes: original LUT, known-DC oracle, plain-mean
centering, circle centering and qualified-circle centering. The comparison
uses real bit-exact U85Q-LP; it does not infer RF sensitivity or displayed-line
lock from improved luma SINAD.

For injected DC `1.5 - 0.75j` cells:

| True C/N | Median/max circle error | Qualified cases | Median original / centered luma SINAD |
| --- | --- | --- | --- |
| 2 dB | 0.249 / 0.288 cells | 0/12 | -0.61 / -0.61 dB (unchanged) |
| 10 dB | 0.026 / 0.055 cells | 12/12 | 4.33 / 6.50 dB |
| 26 dB | 0.050 / 0.073 cells | 12/12 | 5.05 / 9.84 dB |

The gate avoids learning a new centre during those weak 2-dB scenes. Whether
the last qualified centre remains appropriate after a gain/filter change is
a controller question; it must not silently carry a stale correction across
contexts. Zero-DC LUT construction is exactly the original table. All eight
kp levels retain every worker phase/DAC bit under the tested translations.

For large DC `4 - 2j` cells all 36 stress scenes are refused. At 26 dB, even
the exact known-DC oracle changes median luma SINAD only from -5.55 to -4.30 dB.
ADC folding has already lost information. Software centering cannot promise
immunity to arbitrary analog DC or remove the need to correct sufficiently
large offsets before quantization.

Results: `research/data/dc_live_workaround.json` records all synthetic and
captured cases, actual-C estimates, qualification and negative/oracle results.

## Alternatives and the simpler choice

| Approach | Assessment |
| --- | --- |
| Hardware native DCO tracking | Current vendor/PBUS evidence does not establish a safe continuous FM DC loop. Leaving debug hold is a gain/DC ownership change, not proof that vendor tracking converges. Do not enable unknown registers on that assumption. |
| Existing carrier-circle DAC search | Works without a quiet channel but deliberately perturbs DC for about two seconds and needs a measured DAC response. It cannot meet the requested uninterrupted workaround by merely running it more often. |
| Plain IQ mean | Exact for stationary zero-mean noise, biased by finite FM phase dwell or a near-zero-frequency/partial arc. It is not a universal carrier DC centre. |
| Differences/high-pass before phase | Removes constant DC mathematically, but also changes FM amplitude/phase. For constant frequency its amplitude is proportional to `abs(sin(pi*f/Fs))`, vanishing at zero frequency. Video-dependent frequency and sign changes introduce distortion; the current two LP bundles also have no proven spare subtraction state/path. |
| Qualified circle + phase-token centering | Reuses existing sampled observation and hardware table, introduces no sample CPU work and requires no DAC response calibration. Best small candidate for correctable DC, subject to real controller/board validation. |

An exactly constant captured IQ point cannot distinguish DC from an FM phasor
that does not rotate in the observed window. More clever estimation cannot
create missing information. The appropriate behavior is to retain safe prior
state or report unknown, rather than manufacture a correction.

## Reproduce

Prepare on the existing numpy/scipy/numba environment, then run the actual C
probe in the pinned ESP-IDF image and report:

```sh
python3 research/dc_live_workaround.py prepare /tmp/dc-study
cc -O2 -std=c11 -Wall -Wextra -Werror -Imain research/dc_live_probe.c -lm -o /tmp/dc-probe
/tmp/dc-probe /tmp/dc-study/probes.bin > /tmp/dc-study/probe.csv
python3 research/dc_live_workaround.py report /tmp/dc-study research/data/dc_live_workaround.json
cc -O2 -std=c11 -Wall -Wextra -Werror -Imain test/test_dc_live.c -lm -o /tmp/test-dc
/tmp/test-dc research/data/walk_2026-10-13
```

Actual-C probe and helper tests pass under pinned IDF v6.0.2. Firmware
integration, CPU deadlines, fixed-gain real-carrier A/B, filter/gain change
handoff, fading and output/display acceptance remain to be completed.
