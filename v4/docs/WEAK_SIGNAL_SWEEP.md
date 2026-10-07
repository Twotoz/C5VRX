# Weak-signal collapse: separate C/N from IQ occupancy

Extends **C5VRX by Twotoz and the C5VRX contributors**, the PR185
detector study, passive DAC model and staged gain research.
Source: https://github.com/Twotoz/C5VRX; official website and Discord invite:
https://twotoz.github.io/C5VRX/. Existing author notices and GPL-3.0-only apply.
Baseline: PR185 `436d8ec55aa274c36bed54ab4e5673d51298589d`.

## Operator target

Strong input already looks good. With weak input, the picture becomes grey
and grainy, then disappears. Improve that transition while preserving strong
picture quality. This report diagnoses the synthetic demod path; it does not
identify the physical board's cause or deliver a firmware fix.

The old `raw_at` model holds noise amplitude fixed while increasing signal
amplitude with C/N. That models one legitimate fixed-gain sweep, but mixes
quantizer occupancy with C/N when interpreting the resulting quality curve.
The new diagnostic independently varies expected total complex IQ RMS:

```
rho = 10**(CNR_dB/10)
IQ = RMS * (sqrt(rho)*signal + noise) / sqrt(1+rho)
```

Signal and noise each have unit mean power. Their expected powers sum to RMS
squared. No per-realization normalization or automatic gain optimization uses
the test stream. This is an idealized occupancy control, not an RF gain index
or a model of the physical AGC/noise figure. It cannot improve analog C/N.

## Measurement

- Three unchanged references: HC50, VLP56 and OVP56. Same raw input, passive
  DAC conductance model and 5-MHz output filter for every detector.
- Fresh diagnostic seeds 1801/1802/1803; random detail, bars and multitone.
  C/N -2/0/2/4/6/8/10/12/14/18 dB; IQ RMS .75/1.5/3/5 cells.
  65,536 source samples at 80 MS/s, acquired at 40 MS/s: 1,080 model cases.
- Delay, gain and offset are calibrated on a noiseless counterpart with the
  same carrier amplitude, then frozen for the noisy case. MSE includes level
  and DC error. The old noisy gain-fit score is reported separately so it
  cannot hide amplitude collapse in the primary score.
- Coherent video contrast is the calibrated output/truth covariance divided
  by truth variance, expressed as percent. It is not chroma saturation.
- Origin and rail occupancy are reported per 1,000 samples. No DC/skew stress
  is injected in this first isolated sweep.

The illustrative gate is SINAD >=6 dB AND errors greater than 40 IRE <=10 per
1,000 samples, on seed-mean results at the stated and every higher tested C/N.
It is deliberately labelled illustrative: it is not a goggle lock requirement,
an interpolated threshold, a per-seed guarantee or a physical sensitivity claim.

## Results

Lowest tested C/N passing the illustrative gate for random-detail video:

| IQ RMS (cells) | HC50 | VLP56 | OVP56 |
| --- | --- | --- | --- |
| 0.75 | 14 dB | No pass through 18 dB | No pass through 18 dB |
| 1.5 | 10 dB | 10 dB | 10 dB |
| 3 | 8 dB | 8 dB | 8 dB |
| 5 | 8 dB | 8 dB | 8 dB |

The 2-dB grid difference between 1.5 and 3 cells suggests checking IQ occupancy
before expecting a new LUT to cure the observed collapse. It is not 2 dB of
measured RF improvement or a reason to blindly raise gain. At 5 cells, low-C/N
rail occupancy increases; the physical fine lanes also fold rather than clip
like this simplified quantizer.

At 3 cells and 6 dB C/N, OVP56 random-detail SINAD is 6.19 dB but still has
21.89 large errors per 1,000 samples. At 8 dB those become 8.31 dB and 4.46.
Thus a reasonable average SINAD alone can conceal the error tail around the
collapse. The three references' full scenario curves are in
[weak_signal_curves.csv](weak_signal_curves.csv).

At 3 cells, OVP56's coherent random-detail video contrast falls from 95.9%
at 14 dB C/N to 79.1% at 4 dB and 54.1% at 0 dB. This reproduces an amplitude
loss mechanism in the synthetic model consistent with a grey-looking picture;
it does not establish the cause of the operator's board/display symptom.

These seeds are diagnostic data, not untouched finals for a future optimizer.
No candidate is trained, selected, promoted or flashed. Future tuning informed
by these results needs new final seeds.

The follow-up fixes unsigned arithmetic in the host `designs.cells` IQ
unpacker: negative components of uint8 input previously wrapped to 248..255.
The CSV is regenerated after an exhaustive 256-byte regression. LUT-based
OVP56/VLP56 quality numbers are unchanged; HC50 and origin/rail diagnostics
are corrected. The earlier unsigned-IQ theory runs are invalidated, not
reported as evidence. See [MEGA_DEMOD_STUDY.md](MEGA_DEMOD_STUDY.md).

## Next physical discrimination

At a fixed channel, VTX content, demod and receiver setup, compare strong,
grey/grainy, and just-lost states. Record actual gain and gain transitions,
IQ median/spread, origin and rail/folding indicators, phase coherence and
transport faults. Span75 sync estimators are gated in PR185's pair modes;
their printed values must not be treated as real sync measurements.

If occupancy collapses while gain remains low, investigate gain recovery using
the existing physical step/settling guards. If occupancy is healthy while C/N
deteriorates, focus on demod error tails and the signal/noise bandwidth tradeoff.
If the composite waveform remains usable but the display loses lock, measure
its sync/burst waveform before changing the discriminator. The symptom alone
does not choose among these causes.

## Reproduce and limits

From repository root, with offline NumPy/SciPy installed:

```sh
python v4/tools/detector_study/test_weak_signal_sweep.py
python v4/tools/detector_study/weak_signal_sweep.py --output /tmp/weak-signal
```

The command writes individual `measurements.json`, aggregate `summary.json`
and `curves.csv`. Tests check fixed total power, known C/N, calibration,
contrast/DC collapse and occupancy. Firmware remains raw Q4/I4 RX40 -> raw32K
-> TX-only BitScrambler, two bundles/50 ns, CVBS20 duplicated at DAC40.
No live CPU sample processing or new firmware control loop is introduced.

The model has synthetic periodic horizontal sync and AWGN, not full PAL/NTSC
fields/chroma, multipath, real fine-lane folding, PHY gain transients or a
measured loaded DAC/goggle transfer. Clean calibration is an offline diagnostic
reference, not an implemented CVBS servo. Physical sync survival, true RF
sensitivity and strong-signal board acceptance still need measurement.
