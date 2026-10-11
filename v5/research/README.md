# Where can C5VRX-5 firmware still gain range?

For the follow-up automated **architecture search**, see
[DEMOD_SEARCH.md](../docs/DEMOD_SEARCH.md) and `demod_search.py`. It uses the
actual ultrafine folding quantizer and per-case sync/quality rejection gates.
The older tables below retain their original clipped-quantizer assumptions;
they are not measured receiver sensitivity or a guarantee of field range.

Simulation study, 2026-10-11. `sim.py` models the whole C5VRX-5 chain with
the **bit-exact U85Q LUT** (two LUT16 lookups per 50 ns, `u85q.bsasm`), the
C5 analog channel (5th-order, ~20 MHz), unfiltered 80 -> 40 MS/s decimation,
4-bit ultrafine quantisation at the Direct Gain V5 radius, the resistor DAC,
a 5 MHz goggle input filter and a goggle sync/H-PLL model (ideal and
peak-clamp AGC). `range_study.py` runs the experiments; raw rows and tables
are in `data/`. Synthetic numbers, not measured range.

Each configuration gets two thresholds over a C/N sweep (4 cases per point,
PAL and NTSC, C/N per 20 MHz channel):

- **lock**: the goggle still shows 90 % of the lines correctly;
- **usable**: the luma SINAD reaches 0 dB (below that the picture is noise).

The range limit is the worse of the two. Free-space range scales with
10^(dB/20): 3 dB is 1.41x the distance.

## 1. Sync is not the bottleneck

| configuration (deviation 1.0) | lock | usable |
| --- | ---: | ---: |
| 20 MHz channel | 1.80 dB | 3.55 dB |
| 14 MHz filter | 0.85 dB | 2.77 dB |

The goggle keeps lock 1.5-4 dB below the point where the picture is still
usable. Perfect sync (true timing pasted in) moves lock below -3 dB, but a
realistic regenerator (a slow line PLL on the received video, writing clean
syncs where it predicts them) gains nothing: it sees the same noisy syncs
the goggle already flywheels over. A sync flywheel is not a range lever.

## 2. The picture limit depends on the VTX deviation

Usable threshold, then the combined range limit (worse of lock/usable):

| deviation | v5.0 (14 MHz, kp 0.85) | best | setting | gain |
| --- | ---: | ---: | --- | ---: |
| 0.69 | 4.86 dB | 1.66 dB | 10 MHz filter + kp 0.5 | **3.2 dB** |
| 1.0 | 2.77 dB | 1.10 dB | 10 MHz filter + kp 0.6 | **1.7 dB** |
| 1.4 | 2.31 dB | 2.08 dB | 12 MHz filter + kp 0.85 | 0.2 dB |

A narrower pre-detection filter lowers the noise bandwidth; a lower tracker
loop gain narrows the tracking bandwidth (classic FM threshold extension).
Both suit low-deviation VTXs and hurt wide ones: the low-deviation setting
on a deviation-1.4 VTX loses the picture (0.71 at 4 dB) and sharpness
(SINAD 6.9 vs 14.6 dB at 25 dB C/N). The full grid is in
`data/matched.txt`.

## 3. Strong signal and carrier error (`data/strong.txt`)

- 12 MHz + kp 0.85 (the new default) is at least as good as 14 MHz for
  every deviation at weak signal and loses <= 0.8 dB SINAD close in.
- The matched settings cost 0.9-1.7 dB SINAD at 25 dB C/N (still 10-12 dB,
  a good picture) and win from 12 dB C/N downward.
- A residual carrier error of -0.5 MHz hurts the narrow settings at the
  edge; the AFC holds +-50 kHz, so this is margin, not the operating point.

## 4. Tracker gain alone (`data/tracker.txt`)

kp 0.7 / 0.6 / 0.5 on the 14 MHz filter: up to 2.4 dB better usable
threshold at deviation 0.69, about equal at 1.0, worse at 1.4. Only useful
together with a deviation measurement.

## 5. What C5VRX-5 does with it

- Default filter class >= 12 MHz (was 14 MHz): safe for every VTX.
- **VTX-matched mode**: the AFC already measures the sync tip and the
  burst-free porch of every line; their distance is deviation x 1.914 MHz.
  After 32 tight estimates on a usable carrier the receiver picks
  10 MHz + kp 0.5 (deviation <= 0.85) or 10 MHz + kp 0.6 (<= 1.15), else
  keeps the default. Once per channel session; a retune or reboot starts
  over. The kp variants are the same untrained formula compiled with the
  original generator (kp 0.85 reproduces `u85q.bsasm` bit-exactly).

## 6. Open field: AFC re-acquisition at weak signal (`afc_bias.py`)

Operator report (2026-10-11): good video indoors through walls, poor range
in an open field. One firmware mechanism acts exactly at long range. After
4 windows without confirmed sync (200 ms) the AFC drops its lock and gets a
fresh budget of 4 retunes of up to 250 kHz. Losing the lock alone changes
nothing: the offset stays and U85Q keeps demodulating. The risk is the
retunes made after the lock is lost.

The porch estimate is a mean of wrapped phase steps. Below the FM
threshold, clicks pull it toward the receiver centre, and the porch target
is -1.0 MHz. Model results (true error 0, median of 16 windows, mean of 40
decisions):

| C/N (20 MHz) | 20 MHz chan | 12 MHz class | 10 MHz class | MAD gate passes (12 MHz) |
| ---: | ---: | ---: | ---: | ---: |
| 0 dB | +254 kHz | +177 kHz | +138 kHz | 52 % |
| 2 dB | +120 kHz | +77 kHz | +65 kHz | 100 % |
| 4 dB | +58 kHz | +27 kHz | +14 kHz | 100 % |
| 6 dB | +20 kHz | +5 kHz | +2 kHz | 100 % |

The deadband is 50 kHz. Around the usable-picture threshold (~1-5 dB) the
AFC can therefore step the wrong way: up to 4 x 250 kHz per lost lock, in
a fade-heavy field. Section 3 shows that a -0.5 MHz error already hurts the
narrow settings at the edge.

The firmware now retunes only when the C/N estimate is >= 6 dB
(`main/cn_estimate.h`). A weak window still feeds the estimate history. The
VTX carrier does not move during a fade, so the offset found on a strong
signal stays. `[AFC]` logs every retune; `afc_weak_skips` in `T` counts the
retunes that were blocked.

This model omits burst detection and sync search at that C/N. Whether such
steps happen on the board shows up in the logs (`[AFC]` lines,
`afc_step=1` in FIELD_DIAG).

### Coherence is not C/N

The observer coherence of receiver noise alone depends on the analog
filter: about 42 % through the ~20 MHz chip channel, 58 % through the 12 MHz
class and 61 % through the 10 MHz class. Fixed coherence thresholds
therefore mean a different C/N per filter class. `afc_bias.py --coherence`
gives the tables behind `cn_estimate.h`. Profile gating, the diagnostics
and the AFC gate use this estimate.

## 7. Open field: propagation, the larger effect

These are textbook two-ray numbers (ground reflection coefficient -1), not
C5VRX measurements. They show why a test with both antennas low can lose
range in an open field, while short indoor paths through walls work.

| heights TX / RX | breakpoint | extra loss vs free space at 300 / 500 / 1000 m |
| --- | ---: | --- |
| 1 m / 1 m (walking test, VTX in hand or on the ground) | 77 m | +2 / +6 / +12 dB |
| 1.5 m / 1.5 m | 174 m | -4 / 0 / +5 dB |
| 30 m / 1.5 m (flying) | 3.5 km | within +-6 dB nulls and peaks |

The first Fresnel zone has a radius of 2.0 m at the middle of a 300 m path,
and 3.6 m at 1 km. With antennas at 1 m, the ground fills part of it.

Free-space budget, for scale: 25 mW, 2 dBi each side, 3 dB polarisation
loss (linear XIAO antenna vs RHCP VTX), 3 dB C/N per 20 MHz at the
threshold. That gives ~1.0 / 0.7 / 0.46 km for a receiver noise figure of
5 / 8 / 12 dB. Indoors, distances are ~10x shorter (20 dB less path loss),
which covers a few walls. A good indoor picture and a short open-field
range are therefore not contradictory. Neither proves anything about the
demodulator.

`docs/FIELD_TEST.md` separates propagation (height and position A/B),
antenna/polarisation and receiver sensitivity (conducted attenuation).

## 8. Open field: Direct Gain through fast nulls (`test/test_gain_fade.c`)

A moving drone passes multipath and ground-reflection nulls lasting about
λ/2 ÷ speed (2.6 cm, ~3 ms at 10 m/s), after which the carrier is back at
its old level. Direct Gain V5 jumped to the table maximum on the first
empty window. From G60 that is about +22 dB, but the ultrafine window has
only ~4 dB of headroom above the healthy radius, so the returning carrier
folded. The 1 ms burst gate plus a staged recovery added time on top.

The real `dg_tick()` was run on a rough receiver model (gain from the
decoded tuples, noise at P50 ~9 at the maximum, fold above 7.5 cells):

| event (carrier healthy at G60) | before | fade hold 10 ms |
| --- | --- | --- |
| null 1-3 ms, -40 dB | +1.8 ms after the carrier returns, 4 writes | 0 ms, 0 writes |
| null 1-3 ms, -12 dB | 0-1 ms, 4-9 writes | 0 ms, 0 writes |
| null >= 10 ms | +1.8 ms, 4 writes | unchanged |
| carrier stays 12 / 22 dB weaker | 1.2 / 10.6 ms | 11 / 20 ms |
| VTX off | table maximum | table maximum (after <= 10 ms) |
| +20 dB jump | cut at once | unchanged |

The price is up to ~10 ms longer recovery when a carrier stays weaker, for
example behind an obstacle. A 100 ms hold cost 81 ms there, so it was
rejected. These are model numbers; every gain write is also a short PHY
transient on the board. `fade_holds` in `T` counts the windows held.

## 9. Open field: what gives the most range (`field_antenna.py`)

This section uses textbook physics and data from datasheets and listings,
not C5VRX measurements.

**The receiver chip.** The ESP32-C5 802.11a 6 Mbps sensitivity is
-92.5 dBm (module datasheet). That puts its noise figure at about 5 dB,
the same class as an RX5808 (-90 dBm datasheet). At the threshold, Direct
Gain holds a radius of ~4 cells, so the 4-bit capture costs ~0.1 dB there.
The chip is not what limits open-field range.

**The antenna.** Retail listings give the FPC antenna bundled with the XIAO
ESP32-C5 as a 2.4 GHz part (2.9 dBi). Its 5.8 GHz performance is unknown
and could be poor; this needs an A/B test.

**Polarisation against the ground bounce.** At FPV grazing angles
(0.4-4 degrees), the ground reflection of the RHCP VTX signal returns
almost entirely left-handed. Over average ground it is -0.2..-2 dB in the
opposite hand and -16..-34 dB in the same hand. An RHCP receive antenna
therefore rejects the bounce, limited by its axial ratio. A linear antenna
loses 3 dB on the direct wave and still gets the full bounce, which causes
the two-ray nulls.

Model ranges (25 mW, 2 dBi RHCP VTX, -93 dBm, 95 % of positions):

| receive antenna | VTX 1 m / RX 1 m | VTX 30 m / RX 1 m | VTX 30 m / RX 3 m |
| --- | ---: | ---: | ---: |
| linear, 0 dBi | 400 m | 550 m | 400 m |
| linear, 6 dB worse (mismatched antenna) | 300 m | 250 m | 250 m |
| RHCP omni 3 dBi (13 dB cross-pol) | 1250 m | 1150 m | 1150 m |
| RHCP patch 8 dBi (aimed) | 2600 m | 3150 m | 2850 m |

**Other levers.**

- VTX power: 25 to 200 mW is +9 dB, about 2.8x range.
- A low-noise LNA right at the antenna (NF ~1 dB, e.g. ADI CN0534: 23 dB
  gain, 2 dB NF) adds ~3-4 dB over a ~5 dB chip NF, minus coax loss. A
  ~5 dB NF LNA adds little (VK4GHZ build).
- Spatial or polarisation diversity needs a second receive path (a second
  C5 plus a video switch).
- Firmware cannot turn a linear antenna into a circular one.

## 10. Wide VTXs: the default 12 MHz class clips them (`range_study.py wide`)

On the board's own VTX the sync tip sat at -5.0 MHz and white at
+6.25 MHz (docs/FINDINGS.md). By the firmware's formula, that 11.25 MHz
swing is a deviation of ~1.7, wider than the 0.69-1.4 studied above.

Range limit (worse of lock and usable), bit-exact U85Q, kp 0.85, C/N per
20 MHz (`data/wide.txt`):

| deviation | 12 MHz (default) | 14 MHz | 16 MHz | 20 MHz chan |
| ---: | ---: | ---: | ---: | ---: |
| 1.4 | **2.08** | 2.31 | 2.54 | 3.06 |
| 1.7 | 3.64 | 3.28 | **3.26** | 3.63 |
| 2.0 | 8.03 | 5.98 | 5.27 | **5.18** |

Picture quality at 8 dB C/N (SINAD) for deviation 1.7 is 5.9 dB at 12 MHz
and 7.9 dB at 16 MHz. A calmer tracker (kp 0.70) is worse for every wide
VTX. The firmware adds a WIDE profile: the same tracker (kp 0.85) with a
filter class >= 16 MHz from the same sweep, chosen at a measured deviation
>= 1.6. The break-even is ~1.55, so a wrong WIDE on a narrow VTX costs only
~0.5-0.8 dB. The host test sends no VTX <= 1.45 into WIDE.

The filter blob gets a new version, so the receiver runs one new filter
sweep after the update (startup, as before).

## 11. Where it is good and where it is bad (`situations.py`)

This stress matrix runs the bit-exact U85Q with the profile the firmware
picks per deviation, at 2 seeds and C/N 4 / 8 / 20 dB (`data/situations.txt`;
the focused comparisons used 4 seeds). Picture is the share of lines the
goggle shows correctly.

**Good** (picture ~1.0 from 4 dB): clean signal, residual carrier error of
+-0.5 MHz, motion fades (3 ms nulls, picture kept, SINAD lower), DC 0.5
cell, I/Q imbalance 1.5 dB / 10 degrees, a CW spur at -10 dBc, and a strong
signal at the top of the gain band.

**Bad, and what was done about it:**

- **Negative carrier error at weak signal.** At 4 dB C/N, -0.75 MHz drops
  the picture to 0.86-0.89 and -1.0 MHz to 0.5-0.75 (dev 1.7), and
  -1.5 MHz loses it (0.03-0.58, all profiles). The sync tip then runs into
  the low edge of the filter and the U85Q window. Positive errors keep the
  picture but cost SINAD.
  - Firmware flaw found in the AFC (`test/test_afc.c`): the correction
    budget (4 x 250 kHz) only came back after a lost lock. A VTX more than
    1 MHz off therefore never got centred and never locked. It kept
    0.2-0.45 MHz residual for the whole session.
  - Fix: while still unlocked, a spent budget re-arms after 3 s. The
    offset clamp is widened from +-1.5 to +-2.5 MHz (channels are >= 19 MHz
    apart). VTXs up to 2.4 MHz off are now centred within 20 s; a centred
    carrier with 80 kHz estimate noise is never retuned. The +-2.5 MHz
    range of the vendor offset helper is not yet hardware-tested.
- **Long multipath echoes** (-6 dB at 0.3 us, -3 dB at 1 us) hurt every
  profile alike. A wide VTX loses the picture with either echo, and every
  VTX loses it with the 1 us echo up to 8 dB. This is frequency-selective
  fading. The BitScrambler cannot run an equaliser, so this is not a
  profile or tuning choice. Open-field ground reflections have nanosecond
  delays (flat fading, sections 7 and 9), so these echoes come from
  buildings and trees.
- **DC of 1 cell** hurts wide VTXs at 4 dB (picture 0.36). The measured
  uncorrected DC is ~0.13 cell, and the per-gain DC hold covers it.
- **WIDE vs DEFAULT on a deviation-1.7 VTX:** equal or better in every
  situation (+2 dB SINAD at 8 dB), so the WIDE profile stays.

## Not firmware levers

- The second IQ40 sample of every span (+0.5..1.1 dB in the C5VRX-4 ledger)
  needs four lookups per 50 ns; the TX BitScrambler does two.
- Antenna diversity, LNA and filters in front of the C5: hardware.

## Reproduce

```
python range_study.py filter  data/filter.json
python range_study.py sync    data/sync.json
python range_study.py tracker data/tracker.json
python range_study.py matched data/matched.json
CNRS=0,2,4,8,12,16,20,25 python range_study.py strong data/strong.json
```

Needs numpy, scipy and numba. `data/tracker_luts.json` holds the kp variant
LUTs (generated with the C5VRX-4 untrained generator of PR #190).

## 10. Demodulator follow-up: U85G (draft, opt-in)

The stacked #196 follow-up screened 252 analytic formulas and built a
near-origin-only PAIR4411 decoder that uses B's signs. Independent full
PAL/NTSC rasters show small AWGN gains on wide VTXs, but a synthetic echo
case regresses badly in sync. It is consequently **OFF by default**, not
a claimed field-range fix. See [PAIR_DEMOD.md](../docs/PAIR_DEMOD.md) for
numbers, rejected candidates, the generator, reproducible data and the
physical A/B gate. Medium/narrow firmware profiles keep U85Q even in an
opt-in build.
