# HDZero: measured native CVBS conditioning / 11p-OFF test build

C5VRX by Twotoz and the C5VRX contributors. Extends issue #200's output-level
findings, the existing AFC native-H detector, P64 bounded replay and PR #218's
flywheel evidence. Based on main `78ff43a`. Physical HDZero acceptance is pending.

## Problem addressed

The nominal demodulator maps a fixed -6..+9 MHz window to DAC 0..63. Cameras/VTXs
with different FM deviation therefore produce different **sync depth and
blanking pedestal**, even after AFC centres their porch. A global multiplier
neither establishes those references nor knows how much white headroom exists.
It can clip a bright scene. Flywheel repair is a separate possible cause of
rolling; `FINDINGS.md` records a board case where OFF restored a stable picture.
These facts do not prove one cause for every affected goggles/camera pair.

This build starts OUTPUT **AUTO** and FLYWHEEL **OFF**. There is no fixed 3x
preset, soft highlight knee, pixel processing or replacement sync raster.

## How AUTO works

1. The existing AFC detector labels native H sync and burst-free back porch in
   an immutable control snapshot. `cvbs.c` replays that bounded Q4 snapshot
   through the **actual P64 LUT**, including its recurrent phase states, and
   averages the emitted DAC voltages inside those labelled regions. This
   measures the demodulated waveform, rather than assuming AFC frequency is
   exactly the output of P64. The live hardware still paces every sample.
2. Qualification requires native video, AFC lock, exact current receiver/gain
   context, noise-referenced C/N >= 10 dB, held gain, low IQ clipping and two
   seconds of stable tip/porch observations (at least 24 good windows, with no observation gap over 150 ms). Weak,
   absent, busy or stale windows discard the pending candidate. No stored fit
   from another VTX or boot is trusted.
3. A **single linear transfer in nominal loaded voltage** places the lower tip envelope near
   ground (without clipping ordinary tip ripple at its mean) and aims for 286 mV NTSC / 300 mV PAL sync depth. The gain is capped
   by the greatest observed white/peak estimate plus margin and a standard
   sync-to-white reserve (140/40 NTSC; 10/3 PAL). The inverse resistor-DAC map
   chooses the nearest output code. There is no highlight knee or selective
   contrast boost. Burst and picture retain the same linear transfer unless
   an excursion exceeds the available DAC span.
4. The complete measured map goes through the existing five-second shared
   restart guard, halted program/LUT verification and checked rollback. A
   failed application restores the last verified map **and** LUT. One accepted
   fit is then frozen for the channel/offset/standard session; a fade cannot pump the
   contrast or trigger periodic output restarts. Manual OUTPUT remains owned
   by the user and AUTO never overrides it.
5. A channel/offset/confirmed-standard change discards the candidate and requests an identity-map
   reset through the same guard before new qualification. During a cooldown,
   the previous verified map can remain for up to five seconds; `CVBS_LEVEL`
   reports `reset_waits_for_shared_guard`. No claim of seamless channel changes
   is made. Changing to another VTX on the same channel requires a reboot.

`T` includes `CVBS_LEVEL`: source tip/porch, reserved peak, planned nominal
sync depth and `headroom_limited`. Before qualification AUTO emits the normal
1x map. This report is observer/nominal-model evidence, not a voltage measured
at the goggle input. AUTO is output ID 10; the original ten multiplier maps
and IDs remain unchanged. P64 program/state bits, raw32K, DAC20/TX40 cadence,
physical DAC pins and resistor values remain unchanged.

## Correct electrical model

The documented DAC has six weighted resistors driving VIDEO, with **200 ohms
from VIDEO to ground**. The display's 75-ohm termination is in parallel with
that resistor. The earlier `research/output_level.py` incorrectly modelled
200 ohms in series and predicted about 0.623 V full scale. Correcting the
network gives about 1.018 V nominal full scale at 3.3 V GPIO. Firmware uses the
same corrected model as `dac_loaded_uv()`. GPIO impedance/VDD, tolerances,
cable and termination still require physical measurement.

Raw P64 DAC codes do vary within a perfectly flat synthetic FM tip. A separate
1 MHz filter check reduces that variation substantially; raw-code histograms
alone do not demonstrate HDZero rejection and do not justify changing P64's
phase-state allocation. This build keeps the validated demodulator intact.

## RF isolation requested by the operator

The PR #254 0.3 dB noise experiment, `phy_11p_set(1,0)`, is disabled by
`C5VRX_DISABLE_11P=1` for this build. The helper is never called, including its
non-inverse (0,0) form. Factory analog bytes are the default and rollback
baseline; explicit measured filter profiles still work. `rf_filter_applied()`
accepts/verifies that intended state. Digital RX filter mode 0 remains active.

Filter/DC/noise/gain persistence versions are bumped because the receive
transfer function differs from 11p-ON main. Previously saved calibration is
rejected and rebuilt; returning to main also rejects this build's new versions.
This can run startup calibration again. No physical range claim is made.

Experimental output/flywheel preferences use `hdz_output` / `hdz_flywheel` so
main's ordinary `output` / `flywheel` keys stay intact. First flash therefore
starts AUTO/OFF even if main saved flywheel ON. Deliberate test changes survive
a reboot of this build. RF calibration is shared but version-separated as above.

## Limits and bench checks

This is a receiver-side conditioning patch, not proof of the physical cause on
an affected HDZero. It cannot repair missing camera information, a malformed
vertical interval, a bad load/cable or a downstream decoder fault. A finite
white percentile and standard reserve cannot guarantee a future nonstandard
camera's peak; `headroom_limited` may leave less than nominal sync depth, and
future out-of-range excursions may still clip. A wrongly terminated output or
incorrect resistors cannot be calibrated accurately by this nominal model.
The bounded observer adds CPU work; its target timing/stack must be checked on
hardware. Full CVBS compliance and flight safety are not established by CI.

On the bench, record HDZero model/firmware, AV jack versus module bay, camera,
VTX and PAL/NTSC. After startup, keep strong video present until `CVBS_LEVEL`
reports a fit. Compare AUTO/OFF with 1x/OFF and the previously useful manual
level. Test moving bright/dark scenes, saturated colour, menu entry/exit,
reboot, gradual attenuation, short fades and VTX OFF/ON. Exclude intentional
OUTPUT restart gaps from flicker results. Test flywheel ON only as an explicit
bench comparison and turn it back OFF afterwards.

Scope the actual 75-ohm-loaded DAC output: tip/porch/white/burst levels, H pulse
widths, vertical serrations/equalizing pulses and field timing. Compare a
known-good receiver and another display. Attach scope/video and `T`/flight
logs; only those observations can confirm the remaining physical root cause.
