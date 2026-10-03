# Analog field-locked gain timing: native AGC and Direct Gain V5

C5VRX by Twotoz and the contributors. This work extends Leon Beekveldt's native AGC research:
[native-agc-v2.md](native-agc-v2.md) (packet-AGC restart mechanism),
[native-agc-paced.md](native-agc-paced.md) (the `7030[29]` gate and 1 ms pace) and
[native-agc-analog-patch.md](native-agc-analog-patch.md) (PR154: acquire, then
event-driven BB hold; release in a measured blanking interval; immediate
overload rearm). C5VRX-4 only. Direct Gain V5 stays the default owner; it
uses the same field lock to time its ordinary writes (below). Native AGC
stays opt-in (`N`): AGENTS.md forbids making it the default without a new
hardware comparison that beats V4.

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

## Direct Gain V5 (default owner)

Every gain or lane step is a transient: the gain-dependent DC/IQ offset and
the analog settling move the IQ centre and level, and the six sequential lane
routing writes briefly mix two bit slices. Before this change V5 wrote such
steps immediately, at any picture position, at up to a 200 us cadence.

With field lock, an ordinary V5 decision (tracking step or finer-lane
upgrade) is held and written at the next VBI slot (same 615 us offset, at
least 600 us ahead, <=400 us busy wait in the observer). No new decision is
taken while one is pending, so the controller never judges pre-write windows
as a write without effect. A missed slot moves to the next field, never into
the picture. A changed profile/PHY/gain epoch/table drops it; an overload
emergency supersedes it.

Writes stay immediate when the picture is already breaking up: sentinel
overload, lane fold escape, clip >= 20 pm, p95 >= 80, origin >= 350 pm or
coherence < 30, and always without lock (no video, cold start after a
channel change). Ordinary steps therefore wait at most one field
(20/16.7 ms); FM output is amplitude-independent until clipping or
near-origin phase noise, so a small drift does not produce static during
that wait. The level difference between two gains remains: it becomes a
clean step between fields instead of a transient in the picture.

## Controls

`|` toggles field-locked timing for either owner (RAM only, default on):
native VBI releases vs periodic pace, or V5 VBI writes vs immediate writes.
`~` still toggles paced / continuous native AGC. `T` prints `C5VRX4_VBI`
(owner, mode, standard, broad-pulse events/matches; native releases, urgent
releases, late skips, demands, learned centre) and, under V5,
`C5VRX4_V5_VBI` (deferred, applied in VBI, rescheduled, immediate urgent,
immediate unlocked, dropped).

## Evidence boundary

Host tests only: synthetic PAL/NTSC CVBS FM through the Q4/I4 phase LUT,
including dark/busy/bright-object scenes, noise, false events, lock expiry,
the gate ISR sequence, hysteresis, deep-fade/overload urgency and the
register-free V5 slot service. The V5 deferral in `video.c` is covered only
by the firmware build. No RF capture, picture comparison or
range measurement has been made. Bench acceptance: native boot, `T` shows
`mode=vbi_hold` (native) or `mode=gain_writes_in_vbi` with rising
`applied_in_vbi` (V5) and the correct standard. A/B `|` on the same channel for
dashes/noise, check that no rolling or vertical-sync loss appears in the
goggles, and check fade and overload recovery and NO_CARRIER return to high
gain, for PAL and NTSC.
