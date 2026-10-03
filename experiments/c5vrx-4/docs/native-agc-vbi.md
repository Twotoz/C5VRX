# Native AGC for analog video: field-locked VBI releases

C5VRX by Twotoz and the contributors. This work extends Leon Beekveldt's native AGC research:
[native-agc-v2.md](native-agc-v2.md) (packet-AGC restart mechanism),
[native-agc-paced.md](native-agc-paced.md) (the `7030[29]` gate and 1 ms pace) and
[native-agc-analog-patch.md](native-agc-analog-patch.md) (PR154: acquire, then
event-driven BB hold; release in a measured blanking interval; immediate
overload rearm). C5VRX-4 only, native opt-in (`N`); Direct Gain V5 stays the
default owner and never starts this code.

## Problem

The C5 native AGC is an 802.11 packet AGC. On a continuous FPV carrier it
re-acquires from its start gain and traps a different gain each time. The
previous C5VRX-4 native mode opened the gate for 20 us every 1 ms, with no
relation to the picture. Each opening put a 2-3 us saturated acquisition at a
random picture position and changed the noise texture every ~16 lines, which
shows up as noise and dashes.

## Route

Raw ring -> completed 4092-byte window (read-only, overwrite deadline) ->
`native_vbi_task` (CPU, control only, 1..3 ms dithered) -> `native_vbi.c` ->
`pipeline.c` gate ISR. The 40 MS/s IQ, TX BitScrambler and DAC path are
unchanged; no sample is held, replaced or filtered.

1. **Vertical sync detection.** 1-us mean frequency from Phase8 endpoint
   steps; a band at the sync tip (1/6 of the window span). Only a sync run of
   >=15 us counts: vertical broad pulses last ~27 us, ordinary/equalizing sync
   4.7/2.35 us. A dark picture with a small bright object, busy picture,
   equalizing/blank lines, noise and an unmodulated carrier never qualify.
2. **Field lock.** PAL 20.000 ms and NTSC 16.683 ms hypotheses, +-200 us
   match, three hits to lock, one second without a hit expires it.
3. **Release time.** 615 us after the detected point, ~700 us after the first
   broad pulse: inside PAL blank lines 6-22 (320..1408 us) and NTSC lines
   10-21 (381..1144 us), never on the vertical sync/equalizing pulses. GDMA
   position inside the active node is estimated at its middle (+-51 us); host
   tests require >=150 us margin. An ISR more than 150 us late skips the
   release instead of opening inside the picture.
4. **When to release.** Once locked the gate stays held. The hardware's own
   trapped level after each release (first three windows after a 2 ms settle)
   is the centre. Two consecutive windows that are lost (V3 no-carrier),
   +3 dB / -3 dB from that centre, or clipping +20 pm request one release in
   the next VBI. Two windows with severe saturation (clip >= 100 pm or
   p95 >= 95) release at the next 1 ms poll without waiting for the VBI.
5. **Fallback.** Without field lock (no video, search, NO_CARRIER after one
   second) the original 1 ms / 20 us pace runs, so the hardware returns to
   its high-gain idle state. Retunes and labs suspend the gate and reset lock.

Firmware never computes, forces or strobes a gain index. Only the existing
`7030[29]` gate is used, as by the previous pacer. Whether clearing that gate
alone (without `phy_enable_agc`'s `702C[23]` strobe) produces exactly one
acquisition is still unverified on hardware.

## Controls

`|` toggles field-locked releases / periodic pace (RAM only, default on).
`~` still toggles paced / continuous native AGC. `T` prints
`C5VRX4_NATIVE_VBI`: mode (`vbi_hold`, `pace_fallback`, `off`), standard,
broad-pulse events/matches, releases, urgent releases, late skips, demands
and the learned centre.

## Evidence boundary

Host tests only: synthetic PAL/NTSC CVBS FM through the Q4/I4 phase LUT,
including dark/busy/bright-object scenes, noise, false events, lock expiry,
the gate ISR sequence and hysteresis. No RF capture, picture comparison or
range measurement has been made. Bench acceptance: native boot, `T` shows
`mode=vbi_hold` with the correct standard. A/B `|` on the same channel for
dashes/noise, check that no rolling or vertical-sync loss appears in the
goggles, and check fade and overload recovery and NO_CARRIER return to high
gain, for PAL and NTSC.
