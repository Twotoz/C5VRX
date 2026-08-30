## Goal

Deliver recognizable, stable, moving analog FPV video—preferably full-color—
from the XIAO ESP32-C5 physical D4..D9 resistor DAC directly to a 75-ohm
FatShark VIDEO input. The eventual quality target is RX5808-class or better,
but that claim requires a controlled physical comparison.

```text
VTX camera -> 5.8 GHz RF -> C5 mode-0 IQ -> WBFM -> raw CVBS -> DAC -> goggles
```

## Current evidence and selected baseline

- PR #6/#9 are the golden PC video reference, including corrected FM/H-sync
  polarity behavior.
- Mode 0 is signed Q10 in bits 0..9 and signed I10 in bits 10..19. Bits 20..31
  are not signal amplitude.
- Adjacent valid IQ represents approximately 80 MS/s. H-sync cadence and burst
  location—not finite-block throughput—verify it during acquisition.
- The first live baseline is a four-interval phase discriminator: 16,384 IQ
  words become exactly 4,096 raw CVBS samples at 20 MS/s.
- 40 MS/s/2:1 is a later physical A/B candidate. 3:1 is not the default while
  16K boundaries remain finite.

## Production live path under test

```text
short ACQUIRE: I/Q DC + H cadence + polarity + sync/blank + burst
    -> fixed coarse phase LUT
    -> boundary-aware BitScrambler WBFM 4:1
    -> acquired, clamped discriminator-to-DAC transfer
    -> local boundary hold at fixed 20 MHz when IQ time is missing
    -> PARLIO six-bit D4..D9 output
```

The strong-signal path preserves received sync, burst, chroma, and luma. It
does not construct RGB/YUV pixels or a software PAL framebuffer. USB IQ and the
known Phase8 PC decoder remain bounded diagnostics only.

The safe default for the next test remains completed immutable mode-0 blocks.
It never discriminates across a finite capture boundary and never changes the
global video clock to hide missing time. Its vendor-call gap is expected to be
large and is reported honestly. Native continuity and pre-arm stay explicit
probes until fresh SRAM epochs, RF dependence, ownership safety, and coherent
phase behavior are proven. PR #25 restart mechanics remain donor evidence, but
producer lead alone is not continuity and is not accepted as timing repair.

## Exact output hardware

The six GPIOs remain XIAO D4/D5/D6/D7/D8/D9 = GPIO23/24/11/12/8/9. Firmware
models the 8.2k/3.9k/2.0k/1.0k/470R/240R ladder, 200R shunt, and 75R load. It
provides static codes 0/18/31/32/62/63 plus legal PAL black, bars/ramp, and
burst tests before RF debugging.

## Definition of done

1. VTX off keeps the goggles locked to legal fallback PAL.
2. DAC static levels and PAL tests match the modeled loaded voltages.
3. On A1/5865, analysis reports valid IQ, PAL/NTSC cadence, represented sample
   time near 80 MHz, polarity, real sync/blank separation, and color burst.
4. `LIVE RAW AV` shows the actual moving camera scene with stable H/V lock and
   no continuous rolling or random inversion.
5. Color locks stably where supplied by the VTX.
6. No repeating 16K seam is objectionable.
7. USB connection and bounded diagnostics do not own or pace normal AV.
8. After the first picture passes, compare 20M/4:1 and feasible 40M/2:1 on the
   same goggles and RF conditions.

Do not close this issue from build or math results. Current physical gate:

```text
AWAITING XIAO + FATSHARK LIVE AV RESULT
```
