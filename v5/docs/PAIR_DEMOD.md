# U85G: near-origin pair assistance (experimental)

Stacked on PR #196, baseline `8819b695c7a9983bda22de4ff7647b5ecd4f0601`.
This is C5VRX by Twotoz and the contributors. It extends PR #190's
`v4/tools/dsp_search/untrained_search.py` and `compile_overlay.py` at
`7491a660e26722afbb9dd9e24e69ba381b4f130d`, not an independent invention
of the continuous IQ path or shared-LUT architecture.

**This does not establish that the operator's outdoor range problem is
caused by the demodulator, or that it is fixed. Hardware validation is
pending. Keep this PR as a draft until the conducted and field A/B passes.**

## What changes

U85Q's PAIR4411 address contains full I/Q from sample A and both signs
from B, 25 ns later. U85Q's analytic encoder effectively ignores B. U85G
uses B only where A has low amplitude and an uncertain phase:

```
direction_B = unit(B) * exp(-j * 0.4)
weight(A) = max(0, 1 - |A|² / 2.5²)
observation = A + weight(A) * direction_B
```

All operations happen **offline**, when generating the 1024-word table.
The rotation is a fixed carrier prior (~2.55 MHz over 25 ns), not a
measured carrier frequency. The observation is encoded with U85Q's same
28 precise-sector tokens and four origin-quadrant tokens. The WIDE
variant keeps the original gains: kp=0.85, quadrant kp=0.5.

For every A cell with radius >=2.5, and every B sign combination, the
encoder token is identical to U85Q. Every state-transition/DAC word's
lower eleven bits is identical to U85Q for **all** 1024 addresses. Only
near-origin observation tokens change. This preserves the frequency
window, DAC transfer and healthy-radius clean-signal behavior by
construction. At smaller healthy radii the new tokens can still change
picture quality: see the envelope data, not just this invariant.

When built with `-DC5VRX5_PAIR_ASSIST=ON`, only the freshly measured
**WIDE** receive profile chooses U85G. The build default is OFF: the
known echo regression blocks promoting the candidate. DEFAULT,
KP060 and KP050 retain their original U85Q programs. A candidate for the
narrow profile was rejected: at deviation 0.80 its usable-luma threshold
improved but goggle lock became worse, increasing the combined limit.
There is no additional menu, live switching, lane change, gain write,
CPU sample loop, or sync generation. Serial `T` identifies the actual
program, including `U85G_KP085_EXPERIMENTAL`.

## Realtime contract

| Property | U85G |
| --- | --- |
| Source | same fixed ultrafine Q4/I4 MODEM_DIAG |
| Acquisition | PARLIO RX40, raw 32 KiB cyclic ring |
| Demod owner | TX BitScrambler only |
| Schedule | same eight controller/worker blocks; two lookups per 50 ns |
| Resident LUT | one 1024x16 table (2048 bytes) |
| State | same 32-phase state, continuous across DMA boundaries |
| Output | same unique CVBS20, duplicated [D,D] at TX40, physical DAC6 |
| Selection | startup/CALIBRATE while status/menu owns TX; never live |

The source schedule is instruction-for-instruction identical to the
board-tested U85Q program. Assembly/build success does not independently
prove board FIFO behavior or sample-gapless RF acquisition.

## Research and negative results

`v5/research/data/pair_screen.json`: 252 analytic formulas scored on 15
110,000-byte PAL chunks each (deviations 0.69/1.0/1.4/1.7/2.0, C/N
2/6/25 dB). Tested PAIR4411 vs PAIR3322, constant B weighting, rotation,
sector centroids, innovation/update/advance output and radius-gated
assistance. No LUT entry is fitted to data. This screen uses luma SINAD
only; it cannot establish better range.

`pair_pilots.json`: 1264 independent full-raster metric rows, including
rejected alternatives. Always-on B weighting improved weak luma but
cost strong-signal SINAD. Conditional hard/cosine agreement gating was
also tested; it removed most luma benefit and still worsened sync in
some cases. Near-origin assistance was retained for a controlled trial,
not declared universally better.

`pair_validation.json`: 608 final full-raster metric rows. Independent
161xxx/171xxx seeds, four seeds per AWGN point, PAL and NTSC, two fields,
C/N 0..8 and 25 dB, deviation boundary cases 0.80/1.60, ±0.5 MHz residual
carrier errors, RMS 2.5/5.5, 25/100 ns echoes and 1/3 ms fades. Both
noise-free and noisy output use the same physical resistor-DAC model.
The combined limit is max(90% correctly displayed lines, 0 dB luma SINAD).
The inherited goggle model calibrates the clean waveform's scale/latency;
this is not a measured goggle acceptance or voltage-compliance test.

The raw label `CANDIDATE` also retains the rejected narrow prototype.
Firmware uses the candidate only for WIDE; its lower-deviation results
must not be described as firmware improvements.

## Final AWGN result (simulation only)

| Deviation | #196 combined limit | U85G combined limit | Improvement | Strong SINAD #196 / U85G |
| --- | ---: | ---: | ---: | ---: |
| 1.60 | 3.40 dB | 2.86 dB | 0.54 dB | 15.77 / 15.77 dB |
| 1.70 | 3.26 dB | 3.12 dB | 0.14 dB | 15.75 / 15.75 dB |
| 2.00 | 5.32 dB | 5.08 dB | 0.24 dB | 11.89 / 11.91 dB |

The weakest luma threshold improved more than the combined threshold in
some cases; the goggle lock must also pass. This is why the larger SINAD
improvement is not advertised as a larger RF sensitivity gain. A 0.14 dB
free-space budget change would be only ~1.02x distance, even if verified.
The model cannot explain or promise a large outdoor range increase.

**Important negative result:** in the final wide-VTX 25 ns/0.65-amplitude
synthetic echo case at 4 dB C/N, NTSC correctly displayed lines fell from
0.880 to 0.249, despite better luma SINAD. PAL fell 0.938 to 0.875.
The candidate must therefore stay opt-in; it has NOT passed a universal
picture/sync regression gate. Keeping the original quadrant gain (0.5)
was better than the pilot 0.7 variant but did not eliminate this failure.
The rotation prior and use of only B's quadrant remain limitations.

Eight wide-VTX strong-signal envelope cases (±0.5 MHz, RMS 2.5/5.5) had no
lock loss; worst SINAD change was -0.19 dB. Six wide-VTX fade cases gained
0.16..0.4 dB luma SINAD, with worst line-share change -0.017. These are
synthetic examples, not a calibrated model of the field or of gain-control
behavior during a physical fade. The inherited model normalizes average
received carrier power, so echo cases compare demodulation at a given
average C/N; they are not a two-ray path-loss budget.

## Validation and reproduction

- ESP-IDF 6.0.2 ESP32-C5 builds: #196 baseline, pair assist ON and OFF.
- `python v5/tools/gen_pair_demod.py --check`.
- `python v5/test/test_pair_demod.py`: original LUT bit-exact reproduction,
  exhaustive shared-word/high-radius/transition-word invariants, and
  instruction-for-instruction schedule equivalence.
- Existing C host tests for quiet calibration, profile choice, field
  diagnostics and gain-fade handling pass.
- Research: `python -m pip install -r v5/research/requirements.txt`, then
  `OPENBLAS_NUM_THREADS=1 python v5/research/pair_screen.py screen.json`
  and `OPENBLAS_NUM_THREADS=1 python v5/research/pair_validate.py result.json`.
  The full validation has 304 paired cases / 608 metric rows. `--quick`
  reduces fields/seeds and is not the checked-in result.

## Before promotion

1. Repair the synthetic echo/sync regression without hiding it behind
   aggregate luma scores; revalidate on new seeds and real IQ captures.
2. Build ON and OFF from the same revision and settings. Confirm serial
   `T` shows `PROFILE active=WIDE` and the expected `demod=`; otherwise
   the two builds use the same original program and the test is inconclusive.
3. Use controlled attenuation and the same antenna/VTX/channel/deviation,
   goggles and supply. Compare moving video, colour, sync, DAC swing, C/N,
   gain, RF offset and transport faults. Check both acquisition and an
   already-locked signal fading down and recovering.
4. Field A/B: same route, height, antenna direction and RX polarization;
   repeat with and without nearby walls, include movement and deep nulls.
   Preserve FIELD_DIAG logs and raw IQ snapshots around the cliff. A genuine
   improvement needs repeatable usable moving video farther into attenuation,
   without worse indoor multipath, sync or recovery.

No physical board or controlled RF source was available in this session.
The operator's indoor/outdoor report remains an observation; stronger
indoor reception alone does not identify reflection handling as the cause.

## Correcting the quantizer assumption

The inherited #196 simulator clips/floors a signed 4-bit value. Actual
ultrafine capture selects ADC bits **9,6,5,4**: it omits bits 8/7 and folds
outside its local window instead of simply clipping. This matters especially
for noisy tails near the FM threshold. `sim.quantize_iq(..., 'ultrafine')`
now models the actual selected bits of a signed 10-bit ADC, assuming one
cell =16 ADC counts and saturation at -512..511. The inherited `clip`
mode remains available to reproduce the previous studies exactly.
The host test exhausts all 1024 signed ADC values against the tap bit map.
This establishes the bit mapping, not the physical noise, ADC scale or
filter response of a particular receiver.

**The 252-formula screen, pilots and 608-row table above used the inherited
clipping approximation.** An additional frozen-candidate cross-check uses
the ultrafine folding model (independent 191xxx seeds, two fields, PAL/NTSC,
24 paired AWGN cases / 48 metric rows, `pair_ultrafine.json`):

| Deviation | #196 combined limit | U85G combined limit |
| --- | ---: | ---: |
| 1.60 | 2.86 dB | 3.49 dB (regression) |
| 1.70 | 3.72 dB | 3.45 dB |
| 2.00 | 5.30 dB | 5.05 dB |

Its C/N grid (2/4/6/25 dB) and two seeds are too small for a sensitivity
claim. It reinforces the rejection: a demodulator that looks better in
average luma or a simplified quantizer is not automatically better in
usable moving video. Future searches should use the folding model and
real raw-IQ captures, and screen on sync as well as luma from the start.
Reproduce with `python v5/research/pair_fold_validate.py fold.json`.
