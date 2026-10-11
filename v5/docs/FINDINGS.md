# C5VRX-5 findings

C5VRX-5 keeps only what the C5VRX-4 research on PR #190 (branch
`codex/c5vrx4-pair-range`) showed to help the picture. The full study, its
scripts and data live in that branch under `v4/docs/PICTURE_CLIFF_STUDY.md`,
`v4/tools/dsp_search/` and `v4/docs/data/`. This page is the summary.
Synthetic results are host simulations; board results are labelled as such.
Measured range is not established.

## The problem: the whole picture dropped out

With slightly bad video the C5VRX-4 picture did not get noisy, it vanished.
A goggle model (sync low-pass, slicer, H-PLL flywheel, ideal and peak-clamp
AGC) over full fields showed why: the trained 32-phase trackers (PAIR,
RANGE32) slip on VTXs with a larger deviation (>= ~1.05) or an off-centre
carrier and never re-acquire. They lost the whole picture in 8-25 % of cases
even at 20-25 dB C/N. The board logs added two contributors: the DC search
running under weak video, and gain hunting at the range edge.

## Where information is lost, and where clicks come from

An information ledger added one loss at a time to identical signals:

- The 4-bit IQ lanes, folding included, lose essentially nothing against
  the analog IQ. Using one sample per 50 ns span is not a loss either.
- Clicks are FM threshold physics: the noise phasor circles the origin. The
  ideal analog discriminator (an RX5808) has them too. The IQ shows when
  they happen: the sample lands in the origin cells.
- The loss is the 32-sector phase lattice (625 kHz per output step). A
  tracker with clipping recovers it; the flashed LUT reaches its own float
  bound below 14 dB C/N.

## U85Q: the demodulator

An untrained search chose among closed-form formulas only (no fitted LUT
entry), with held-out confirmation:

- state: 32 tracked phases, `p' = p + 0.85 * wrap(observation - p)`;
- observation: the PAIR4411 shared word (sample A full byte + sample B
  signs);
- origin cells: four coarse quadrant tokens with their own gain 0.5. Erasing
  them (U85E) was sharper but failed under multipath, because fades put
  real signal into the origin cells;
- output: the tracked advance, linear over -6..+9 MHz onto the DAC.

Held-out results (picture = correctly displayed lines, mean):

| model | nominal picture / SINAD | mixed robustness picture / SINAD |
| --- | --- | --- |
| **U85Q** | 0.998 / 3.55 dB | **0.893** / 1.93 dB |
| U85E (origin erasure) | 0.995 / 4.26 dB | 0.706 / 2.48 dB |
| HC50 (stateless) | 0.981 / 2.17 dB | 0.872 / 0.76 dB |

The mixed set covers echo + fading, carrier +-2 MHz, DC, I/Q 1.4/20 deg and
a CW spur. Trained PAIR/RANGE32 scored 0.83 / 0.82 on the nominal set, with
0.00 in their worst cases. Under multipath U85Q matches its float upper
bound; what remains there is in the signal, not the LUT.

The output window is the one trade-off left: narrower is sharper but clips
the sync tip when the carrier is off centre.

## Board validation (2026-10-11, real video, A1)

U85Q with the narrow filter and the DC latch, 12 snapshots:

- the carrier's sync tip sat at -5.0 MHz and white at +6.25 MHz. U85Q's
  -6..+9 MHz window holds both; U85E's -3.5..+7.5 MHz clipped the sync tip,
  so a narrower window is not safe;
- clicks per 1000 samples: U85Q 83.0, U85E 85.7, HC50 101.5.

## AFC target matched to U85Q (C5VRX-5)

C5VRX-4 centred the VTX blanking level at -436 kHz, the design centre of
the trained trackers. U85Q's window is -6..+9 MHz, and the board's real VTX
swung wider than the deviations searched (up to 1.4). Shifting the AFC
porch target, held-out simulation (board conditions with +-1 MHz carrier
error, stress, 20 cases per cell at C/N 4-20):

| porch target | picture (all) | SINAD | deviation 1.4 | deviation 1.8 |
| --- | ---: | ---: | ---: | ---: |
| -436 kHz (C5VRX-4) | 0.908 | 2.42 dB | 1.00 | 0.64 |
| **-1036 kHz (C5VRX-5: -1.0 MHz)** | **0.969** | **3.27 dB** | 0.98 | 0.90 |
| -1436 kHz | 0.961 | 3.89 dB | 0.95 | 0.91 |

The larger shift buys more SINAD but starts to drop pictures; -1.0 MHz is
the balanced choice. Board confirmation is pending.

The Direct Gain V5 band also fits U85Q: same simulation, mean over C/N
4-20 and deviation 0.69-1.4, by IQ radius (cells):

| radius | 3.0 | 3.6 | 4.2 | 5.0 | 5.7 | 6.5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| picture | 0.992 | 0.997 | 0.987 | 0.995 | 0.993 | 0.980 |
| SINAD dB | 3.16 | 3.09 | 3.32 | 3.03 | 2.72 | 1.75 |

The healthy band (P50 13..32, radius ~3.6..5.7, targets 4.1 / 5.2) sits on
the flat part; folding only costs above it. The gain controller is kept.

## The narrow analog filter

FM threshold follows the noise bandwidth before the demodulator. The board
ran ~19.4 MHz (noise bandwidth 20.7 MHz) where Carson's rule for typical
VTX video is ~13-14 MHz. Host results, U85E:

| filter | lock at 2 dB | lock at 4 dB | mean SINAD |
| --- | ---: | ---: | ---: |
| ~20 MHz | 0.66 | 0.92 | 4.26 dB |
| 14 MHz | 0.95 | 0.99 | 4.55 dB |
| 12 MHz | 0.98 | 0.99 | 4.50 dB |

14 MHz costs <= 0.3 dB on a strong signal for every tested deviation, and
helps every weak case. C5VRX-5 measures the setting per board (VTX off,
once) and keeps the one with the lowest noise bandwidth that still covers
14 MHz.

## Receive-chain fixes carried over

- DC: the per-gain DC DAC pair is searched only in confirmed quiet, never
  on a channel where a carrier was seen this boot, and held after every gain
  write (board: DC median ~134 LSB without the hook, ~11 with it).
- Sampling phase: about 3 boots in 10 sampled MODEM_DIAG mid-transition;
  a fourth-difference probe finds it and slips the RX clock.
- Direct Gain V5: healthy-band zero-write zone, 20 ms persistence for
  moderate excursions, burst gate and staged overload recovery.

## What did not help (kept out)

Trained LUT demods, FusionDemod/AutoFit program swaps (3.8-5.1 ms engine
halts), the sync flywheel, the level servo, adaptive lanes, the radius
boost, the vendor RX recalibration (grain on strong pictures), one lookup per
IQ40 sample, frequency-only trackers, MMSE outputs with a narrow window.

## What is left

Within this demodulator class the C5 is close to its ceiling. The remaining
dB are outside the LUT: the RF front end (LNA, filter, antenna, diversity) or
hardware with line memory at sample rate. A flight comparison against an
RX5808 is the open measurement.
