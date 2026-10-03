# Experimental automatic CVBS level regulation

C5VRX by Twotoz and contributors. This follows Leon Beekveldt's report that
output amplitude falls with distance. That report is not yet a measured transfer
curve or proof that RF amplitude directly changes the ideal FM deviation.

## Default and controls

**Enabled by default in PR164 at Leon's request on 2026-10-04.** Lowercase `u`
toggles and reboots; an explicit `c5vrx4/level_lab=0` remains an opt-out.
The original legacy M mapping refuses the actuator for an unchanged comparison.
Use Direct Gain V5: native AGC makes untagged physical gain transitions and the
new observer holds instead of actuating while native owns gain. V/v retain
their video-standard function. T reports requested/ready/blocked, target depth,
update/write/fault counts, worker time and task/heap margin. J estimates the
fixed baseline, not the currently regulated connector volts.

## Reference and control policy

Analog Devices' ADV7180/ADV7181C luma AGC monitors horizontal sync depth and
adjusts digital gain, with bounded gain range. This implementation extends the
same measurement principle; it does not copy or enable the chip's AGC:
https://ez.analog.com/cfs-file/__key/communityserver-discussions-components-files/331/3581.ADV7180_5F00_ADV7181C_LumaGainCalibration_5F00_RevA.pdf
Nominal sync depth is 286 mV NTSC / 300 mV PAL; porch-to-white is 714/700 mV:
https://www.analog.com/en/resources/technical-articles/understanding-analog-video-signals.html

A separate low-priority task wakes nominally every 20 ms. It copies the last
8190 completed raw bytes (204.75 us, more than three line periods) ending at the
start of the active descriptor, including across a ring wrap. Contiguous ring
geometry, active-descriptor stability, <=50-us copy time and a conservative
reuse deadline are checked. Raw source/stream state are never changed. Profile,
PHY, gain and lane must still match under actuator ownership; a 500-us post-write
exclusion covers both snapshot history and settling. Work over 10 ms is refused.
This leaves the existing 50-ms button/menu/AFC timers and the fast RF-gain
observer unchanged. Extra runtime storage is 8190 heap bytes plus a 16-KiB task
stack; allocation failure refuses startup rather than silently omitting it.

The Phase8/winding observer locates negative sync contrast, full H-sync pulse
width, repeated PAL/NTSC line period and the pre-burst porch. Three consecutive
consistent distinct snapshots qualify the servo; a median of their levels
rejects an individual outlier. NTSC period selects 286 mV; PAL selects 300 mV.
For phase delta d, measured black b and sync span s:

`target_uV = 10000 + depth_uV + (d - b) * depth_uV / s`

Sync targets 10 mV and porch 296/310 mV. Thus output **sync separation**, not
received IQ radius or scene brightness, controls the gain. Ambiguous winding
maps to porch. Final codes use the nominal/measured single-75-ohm DAC table and
saturate to its physical range. Colour/video receive the same scale factor.
The target is valid recovered sync depth, not guaranteed full amplitude when RF
information has disappeared. Normalizing the IQ radius cannot restore phase
information lost to noise, quantization or folding.

Reject missing/repeated-invalid sync, plateau MAD over 4 bins or 1/8 of sync
span, origin collapse, clipping, ambiguous trajectories, changed context,
settling and stale/replayed captures. Span remains bounded 12..120 Phase8 bins
(approximately 0.32..3.2x nominal output gain); no unbounded noise amplification.
Invalid evidence holds the last mapping and clears qualification. A gap over
100 ms or a standard change also requires three new valid snapshots. A small
voltage deadband limits one-code hunting. Every update moves each entry by at
most two DAC codes, no more often than 20 ms. It now settles over tens to hundreds
of milliseconds in host fade tests rather than the old seconds-long lab; this
is not a measured hard real-time latency guarantee or line-by-line fast-fade repair.

The matching even-plane entry writes are adjacent, with readback and upper-bit
preservation. They are sequential, **not atomic**. No engine halt, reset, LUT
width change, CPU pixel output, IQ DC subtraction or H/V regeneration is added.

## Hardware access and refusal

Source references, pinned to ESP-IDF v6.0.2:

- [driver load_lut/load_program](https://github.com/espressif/esp-idf/blob/v6.0.2/components/esp_driver_bitscrambler/src/bitscrambler.c)
- [C5 low-level LUT access](https://github.com/espressif/esp-idf/blob/v6.0.2/components/esp_hal_dma/esp32c5/include/hal/bitscrambler_ll.h)
- [C5 register descriptions](https://github.com/espressif/esp-idf/blob/v6.0.2/components/soc/esp32c5/register/soc/bitscrambler_struct.h)

The driver switches LUT width to 32 during loading. The LL header says width is
seen by the engine, not the host, while the register description makes LUT index
units dependent on mode. Neither proves safe concurrent access. Therefore the
lab first probes one changed bit while the engine is halted, checks all 1024
halfword views and neighbouring decoder entries, restores the probe and checks
again. Refusal reloads the pristine program before starting. This checks observed
addressing, not live RAM arbitration or every address combination.

After a successful probe, the controller writes only six DAC bits in each even LUT16
plane, preserving trajectory bits and every odd Phase8 decoder entry. It does
not change LUT width, instructions, phase counters, FIFO state, ring contents or
RUN/HALT during regulation. Each live write is read back; mismatch latches the controller off until reboot. A recursive adapter mutex serializes updates against startup,
menu/lab disable and reload; PHY actuator ownership and fresh epochs exclude
concurrent gain/retune decisions. Source/transport remain IQ40M, raw ring,
TX-only three bundles, unique13.333M and duplicated DAC40M. No per-sample CPU path.

**Still unproven:** concurrent LUT access arbitration, stalls, readback side
effects and waveforms during sequential updates. A startup addressing pass and
host/controller tests do not demonstrate continuous live throughput. The operator explicitly requested default-on for this alpha despite this remaining
physical gate. Readback failure latches it off; transport faults observed after
updates also latch it off until reboot. This is a fallback, not proof that a
transient seam never occurs.

## Bench acceptance

1. Full-flash PR164, STD150 M mapping, Direct Gain V5. With regulation disabled
   through u, record T/J, loaded scope levels and goggle live/recording at strong,
   moderate and weak input. Then enable u (or use its fresh-install default).
2. With regulation enabled, Require startup_probe=pass, ready=1, faults=0. Compare sync/black
   under the same single 75-ohm load and attenuation. Verify dynamic levels
   converge without clipping colour/white or losing line/field lock.
3. Log existing FIFO/transport faults across many updates; compare output timing
   with regulation off. Any new underflow, timing seam or decoder damage blocks
   promotion. Readback success alone is insufficient.
4. Remove signal, inject noise and retune/change lanes: no noise-driven update;
   last map holds until three fresh consistent windows reacquire. Verify menu,
   restart and labs reload a clean baseline with no table race.

H/V sync regeneration/coasting is not implemented. This lab may preserve AV lock
when phase modulation shrinks but cannot guarantee a goggle stays locked once
sync/phase information is unrecoverable.
