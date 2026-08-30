# Production raw RF-to-CVBS test firmware

This implementation starts at `ab79fd0410ef5dc6adb58083a6e72eecb6ed70b8`.
Closed PR #25 was inspected only for low-level C5 rearm and BitScrambler
evidence; its AUTO_AV/raster architecture was not imported.

The physical gate remains:

```text
VTX camera -> 5.8 GHz -> mode-0 Q10/I10 -> WBFM -> D4..D9 -> FatShark
```

PR #6 and PR #9 remain the video oracle. The embedded path uses the same
`angle(x[n] * conj(x[n-1]))` discriminator identity, with the hardware-friendly
four-interval form `angle(x[4k] * conj(x[4k-4]))`. It starts from an 80 MHz
represented IQ timebase and emits 4096 raw composite samples per 16384-word
capture at 20 MHz. Average block delivery rate never changes PARLIO speed.

## Runtime contract

Only `NO_RF`, `ACQUIRE`, `LIVE`, and `HOLDOVER` are production raw-AV states.
ACQUIRE uses three bounded blocks for I/Q DC and four for video structure. It
tests both FM polarities, locks horizontal cadence, measures quiet porch rather
than burst as blanking, measures the burst-band spectral peak, derives the represented
sample clock from line cadence, and builds one clamped 256-entry discriminator
to DAC map. The BitScrambler LUT is then loaded once with the acquired I/Q
origin and is not allowed to track video.

The safe default producer is the bounded vendor mode-0 capture. Every block is
an immutable 16384-word generation. The first output sample is held/neutral, so
no ordinary discriminator crosses a finite RF boundary. While the next block
is unavailable, PARLIO outputs local last-value holdover at its fixed 20 MHz
clock. Missing time is therefore local; valid samples are never globally
slowed. This first safe backend is expected to expose a large vendor-call gap
on hardware and is not claimed to be the final quality producer. Native and
prearm routes remain explicit probes until they pass fresh-SRAM, RF-dependence,
phase-continuity, and ownership gates.

## `phy_chan_dump_cfg_752` static result

The exact ESP-IDF v6.0.2 C5 `libphy.a` was inspected. The symbol is a leaf
three-argument helper in `phy_feature.o`, not an undiscovered buffer-owning
producer. Its complete behavior is:

- `0x600a790c[7:4] = arg0[3:0]`
- `0x600a790c[11] = arg2[0]`
- `0x600a7c00[30] = arg1[0]`

No C5 archive has an undefined reference/call site for it. It does not program
the 64 KiB dump address, length, trigger, enable, DMA, or wrap controls. The
related `phy_chan_dump_cfg` has a different, at-least-five-argument ABI and
only changes `0x600a790c` bits 2, 3, and 7:4. Neither helper justifies a guessed
hardware call, so the probe fails closed. A future probe needs new evidence
linking these selector bits to a fresh continuously advancing IQ destination.

## Electrical output

The exact 8.2k/3.9k/2.0k/1.0k/470R/240R ladder, 200R shunt, 3.3 V GPIO, and
75R load is tabulated for all 64 codes. All six GPIOs use identical explicit
drive capability and one PARLIO parallel transaction. Important modeled loaded
values are 0 = 0 mV, 18 = 296.817 mV, 31 = 498.750 mV, 32 = 518.750 mV,
62 = 1002.317 mV, and 63 = 1017.500 mV.

## Physical gate

Software tests prove math, ownership checks, builds, packaging, and command
contracts only. The PR is deliberately gated as:

```text
AWAITING XIAO + FATSHARK LIVE AV RESULT
```
