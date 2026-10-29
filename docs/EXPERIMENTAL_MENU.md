# EXPERIMENTAL menu (2026-10-29)

C5VRX-5 by Twotoz and the C5VRX contributors. Status: the field-test menu,
NVS persistence, runtime hooks, PHY patch rollback, boot lane choice and
flight-log integration are implemented. Host tests establish software behavior;
physical menu, RF, PAL/NTSC and walk A/B acceptance remain pending.

Goal: A/B-test every receiver option in the field from the goggles menu,
without a laptop or a reflash, and see in the flight log exactly which
settings were on during each second.

## Main menu after the change

BAND, CHANNEL, SCAN, VIDEO, FILTER, OUTPUT, CALIBRATE, EXPERIMENTAL, EXIT
(9 rows, no scrolling). FILTER, OUTPUT and CALIBRATE stay as user settings.

## EXPERIMENTAL submenu

Press = next row, hold = next value. Eight submenu rows scroll below the
EXPERIMENTAL title. The nine main-menu rows fit without scrolling. The
legacy console controls keep working. MAX/fixed gain still never persist.

Implemented runtime items in addition to the table below:

| Item | Values (baseline first) | Exact behavior |
|---|---|---|
| LEVEL TARGET | 0 / -3 / +3 dB | Bias the input level used to score homes by the opposite amount; observations and learning retain their actual scale. |
| LEVEL RELEASE | NORMAL / SLOW / FAST | Slow histogram decay Q8 250 / 253 / 244; fast jump detection and overload handling stay active. |
| GAIN RATE | 0 / 5 / 20 ms | Minimum between upward writes only; emergency downward writes bypass it. |
| GAIN CAP | G83 / G79 / G75 | Limit automatic/live writes to the smaller of the chosen cap and the actual table maximum; explicit calibration owners retain their gain access. |
| LOG CADENCE | 1 s / 5 s / OFF | Sample one second in every 1/5 seconds. OFF suspends automatic logger flash flushes and snapshots; settings changes can still write NVS. |

| Item | Values (default first) | Applies | Where it lives now |
|---|---|---|---|
| GAIN | LEVEL / KEEL / RSSI / MAX / fixed | live | main menu |
| PHY FILT | 12 / 0 / 4 / 8 | live, re-applied after every PHY restore | main menu |
| AFC | ON / OFF | live | main menu |
| DEMOD | P64 / U85Q | verified restart on menu exit | main menu |
| IQ CORR | ON / OFF (RX enables, 0x600A0438 / 0x600A0C0C) | live, re-applied after PHY restore | lab suite 2 |
| PHASE REPAIR | ON / OFF (`phase_service` live scans) | live | always on |
| AUTO PROFILE | REPORT / LIVE | live | console `A` |
| FAST GAIN | ON / OFF (64-sample sentinel path) | live | always on |
| FOLD RADIUS | OFF / ON | live | compile option `LEVEL_FOLD_RADIUS` |
| FOLD MEMORY | OFF / 300 ms / 1 s | live | compile option `LEVEL_FOLD_MEMORY_US` |
| LNA BIAS | STOCK / INIT (0x6A/1/0 high nibble 0xC -> 0x9) | PHY patch | lab suite b |
| CURRENT | STOCK / 0 / 15 / 255 (phy_param+41 via the real setter) | PHY patch | lab suite i |
| LOOP CAP | STOCK / bin 0..6 | PHY patch (bounded PBUS) | lab suite g |
| SPUR 0 | STOCK / +512 / -512 | PHY patch | lab suite h |
| IQ LANE | ULTRAFINE / FINE / COARSE | after reboot, "REBOOT NEEDED" | console `L` (bench only) |
| DAC TEST | step | bench self-test | main menu |
| RESET ALL | | everything to default, plus "REBOOT NEEDED" if the lane changes | new |
| BACK | | | new |

Bench results so far: LNA BIAS, CURRENT, LOOP CAP and SPUR 0 did not move the
receiver noise beyond the shams (PHY_NOISE_RESULTS_2026-10-28.md). Their effect
on the picture was never measured, which is why they go in this menu.

## Deferred field-only candidates

These cannot show anything on a VTX-off bench: they act on the carrier, the
video, the deviation or movement. They are scored with `picture_pct`, the
goggles, and the flight log per setting (A/B on the same walk).

| Item | Values (default first) | Why only the field shows it |
|---|---|---|
| GAIN SYNC | OFF / ON: gain writes wait for the next H-sync tip (max one line, 64 us) | A gain write is a step in IQ; in sync it hides in blanking instead of a torn line. There is no line without video. New. |
| LEVEL TARGET | 0 / -3 / +3 dB | The headroom between noise and fold: +3 means less noise but more folds on peaks, -3 the reverse. Only a moving carrier has peaks. |
| LEVEL RELEASE | normal / slow / fast | How fast LEVEL climbs back after a fade; walk 292 showed the pendulum at the edge. |
| GAIN RATE | 0 / 5 / 20 ms minimum between writes | Fewer steps = fewer glitches, but slower follow; a trade-off you only see while moving. |
| GAIN CAP | G83 / G79 / G75 | The top rows had the same noise floor without a carrier; with a carrier they may fold sooner. |
| FAST GAIN THR | 500 / 250 / 750 per mille clipped | The sentinel threshold: sooner off a strong peak versus fewer false steps. |
| DC SPEED | normal / 2x / 0.5x servo step | The DC moves with each gain step and with temperature; the walk showed edge loss from a slow per-row servo. |
| ANALOG FILTER | stock / -1 / -2 / +1 code | Lab suite 7: narrower = less noise, but clips a wide deviation. Without a carrier only the noise half is measured. |
| TUNE OFFSET | AFC / -250 / +250 / -500 kHz | Where the carrier sits in the filter and relative to the DC notch; with deviation 0.66 the spectrum is asymmetric. |
| AFC SPEED | normal / slow / off-after-lock | A VTX drifts as it warms up; a fast loop also follows video content. |
| SAMPLE PHASE | auto / fixed 0 / 1 / 2 | Bench: all positions equal on noise; on a carrier the slip position decides mixed IQ bits. |
| SYNC CLEAN | OFF / ON | Recognised sync tips out clean (FINDINGS next step 5); sync only exists with video. |
| ERASURE | hold / mid-grey | What the DAC outputs on a folded/uncertain pair: hold the previous code or grey; a choice of picture, not noise. Needs a decoder table variant. |
| LOG CADENCE | 1 s / 5 s / off | Flash writes of the flight log may cause the owned/paused seconds (11 % of walk 300); only a live picture shows a pause. |

LEVEL TARGET/RELEASE, GAIN RATE/CAP and LOG CADENCE above are implemented.
The remaining candidates in this table are deliberately not selectable yet:
the design calls for the next group after first field results. GAIN SYNC also
requires a trustworthy live H-sync trigger: the current CPU observer reads
completed buffered IQ, so simply waiting on its last sync would not align an
RF gain write with the next physical sync tip. No delay-only substitute is
present. SYNC CLEAN and ERASURE need separate decoder work and a verified
restart, rather than live instruction/LUT edits.

## Rules

1. **Defaults are the current firmware.** With every item on its default the
   receiver behaves exactly as it does today.
2. **EXP marker.** While any item is off its default the menu and the status
   screen show "EXP", so nobody flies an experiment by accident.
3. **Persisted.** One NVS blob `exp` (version byte plus one byte per item);
   existing keys (prefs gain/dfilt, afc_off, demod) keep working. The blob is
   accepted only with the correct version/length and every value in bounds.
   Legacy slots in it are reserved zero; snapshots read their actual owners.
4. **PHY patches** are written with readback after every vendor PHY restore
   (`restore_lock` and forced gain writes in rf.c). Patches are first
   restored before vendor channel/offset changes, then reapplied against the
   new stock. Masked unrelated fields are preserved. PBUS waits are bounded
   to 1 ms and the command port is restored. On a readback failure the item
   falls back to STOCK, queues an `exp` event, and shows NOT APPLIED; the
   control task persists the fallback outside PHY ownership. A failed exact
   rollback reboots, as in the lab. CURRENT is the setter's RAM byte, not a
   proven current control. LOOP CAP refuses non-idle/non-debug PBUS; BIAS
   refuses any stock outside the witnessed cf/7f state; SPUR refuses a
   vendor-active slot. Baseline noise exports/control C/N and calibration
   persistence are refused while a transfer experiment is active. The live
   DC servo and gain-map learning are held too, so temporary experiments
   cannot train corrections that are later saved under the baseline context.
5. **IQ LANE** only takes effect at boot (`route_modem_iq` in `rf_start`).
   Off ultrafine the DC servo, the sampling-phase checks and the fold-radius
   estimator are off; the menu says so.
6. **Nothing pauses the receiver** when a value changes, except DEMOD (its
   verified restart already exists) and IQ LANE (reboot).

## Flight log

The format remains append-only V1. Record type 7 is a complete experimental
state (version, count, values), following normal boot settings restoration
and every menu action. Session identity records retain their existing layout.
Every new per-second record includes the schema, mask, **full values**, and a
transition flag. Consequently lost events or ring wrap cannot silently assign
seconds to the wrong settings. Changes inside a second are excluded from A/B
scoring, including an A -> B -> A change. No reduced-cadence record pretends
to average five seconds. Explicit dump/erase commands remain available at OFF.


- **Session-associated state record:** every experimental value; seconds carry the default mask.
- **Event `exp`** on every change: item, old value, new value, readback ok.
- **Per-second record:** the bitmask, schema, full values and transition flag.
- **tools/flight_log.py:** groups `picture_pct`, video %, dBm and gain by
  setting, for side-by-side A/B over the same walk.

## Validation and board acceptance

CI runs the production experiment-state and PHY-boundary harnesses, invalid
blob/default/rollback checks, every menu value/page, the append-only flight-log
round trips, Python grouping/legacy-decoding tests and the existing host suite.
Both standard and PSRAM firmware images must build on the exact PR head.

Physical acceptance (not established by host tests):

1. Flash Full firmware through `tools/flash.py` on an attached board.
2. Visit every item with the goggles, including DAC TEST and BACK; confirm
   menu idle exit and reopening. Verify EXP on menu and status screens.
3. Change/reboot each persistent setting, RESET ALL and confirm baseline;
   IQ LANE reports REBOOT NEEDED until its actual routing matches. On a
   non-ultrafine boot DC servo, phase checks and fold-radius estimator stay
   off; bench L refuses that lane to avoid changing the boot experiment.
4. Check PHY readback across gain changes, retunes and analog profiles. Test
   one option at a time before combinations; inspect failure and rollback.
5. Same-route A/B walks: compare goggles and `_experiments.csv` grouped by
   the exact setting vector. This is observational grouping, not a causal
   claim of better range. LOG CADENCE OFF intentionally provides no samples.

Deferred candidates above require their own implementation and physical
acceptance after these first field results.
