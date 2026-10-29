# EXPERIMENTAL menu (plan, 2026-10-29)

C5VRX-5 by Twotoz and the C5VRX contributors. Status: design plus the first
piece (LEVEL fold memory, compiled in, off). The rest is still to be built.

Goal: A/B-test every receiver option in the field from the goggles menu,
without a laptop or a reflash, and see in the flight log exactly which
settings were on during each second.

## Main menu after the change

BAND, CHANNEL, SCAN, VIDEO, FILTER, OUTPUT, CALIBRATE, EXPERIMENTAL, EXIT
(9 rows, no scrolling). FILTER, OUTPUT and CALIBRATE stay as user settings.

## EXPERIMENTAL submenu

Press = next row, hold = next value. It scrolls like the main menu.

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

## Rules

1. **Defaults are the current firmware.** With every item on its default the
   receiver behaves exactly as it does today.
2. **EXP marker.** While any item is off its default the menu and the status
   screen show "EXP", so nobody flies an experiment by accident.
3. **Persisted.** One NVS blob `exp` (version byte plus one byte per item);
   existing keys (prefs gain/dfilt, afc_off, demod) keep working.
4. **PHY patches** are written with readback after every vendor PHY restore
   (`restore_lock` in rf.c, the same hook as PHY FILT). If the readback
   fails, the item falls back to STOCK and the menu reports it.
5. **IQ LANE** only takes effect at boot (`route_modem_iq` in `rf_start`).
   Off ultrafine the DC servo, the sampling-phase checks and the fold-radius
   estimator are off; the menu says so.
6. **Nothing pauses the receiver** when a value changes, except DEMOD (its
   verified restart already exists) and IQ LANE (reboot).

## Flight log

- **Session record:** every experimental value plus a bitmask of the items
  off their default.
- **Event `exp`** on every change: item, old value, new value, readback ok.
- **Per-second record:** the bitmask, so each second can be scored per setting.
- **tools/flight_log.py:** groups `picture_pct`, video %, dBm and gain by
  setting, for side-by-side A/B over the same walk.

## Build order

1. Fold memory in agc.c (done, `LEVEL_FOLD_MEMORY_US` 0) -> runtime value.
2. `main/experimental.c/.h`: the item table, NVS, defaults, "EXP" bitmask,
   apply/readback per kind.
3. Menu: MENU_EXPERIMENTAL plus the submenu (menu_ui.c, menu.c, control.c),
   host test test_menu_ui.c for every page (no overflow).
4. Live hooks: IQ CORR, PHASE REPAIR, FAST GAIN, FOLD RADIUS/MEMORY, AUTO PROFILE.
5. PHY patches in restore_lock: LNA BIAS, CURRENT, LOOP CAP, SPUR 0, using the
   lab's verified write/rollback code moved into a shared helper.
6. IQ LANE at boot with "REBOOT NEEDED".
7. Flight log: session fields, `exp` event, per-second bitmask, tool and tests.
8. Full local CI, flash via tools/flash.py, board check of every item.
