# A1 live AV-out validation plan — USB-first continuous-ring proof

> Issue #5 implementation order starts with: *prove truly continuous RF
> writer/ring*. Every gate below prints its verdict as ASCII telemetry over
> the native USB Serial/JTAG console — no scope or goggles are needed for
> this stage. Physical AV validation (issue #5 sections 6/7) follows after
> `LIVE START` opens, and is listed at the end.

## 1. Prerequisites

- XIAO ESP32-C5 flashed with this firmware build (`firmware/` bundle,
  profile `xiao-esp32c5`).
- VTX powered, transmitting on a known 5.8 GHz channel (real RF content is
  required for meaningful cadence/phase measurements).
- USB cable connected; Receiver Console open with its command entry
  (or any serial terminal / `tools/c5vrx_lab.py` session).

## 2. Gate chain enforced by `LIVE START` (verified in code)

`LIVE_START` succeeds only when `c5vrx_select_consumer()` returns
`BITSCRAMBLER_RING` **and** `s_mode0_soak_passed` is set. That requires,
in one boot session:

| Capability flag | Set by | Pass condition |
|---|---|---|
| `measured_source_rate != 0` | `PRODUCER_CADENCE_PROBE_0` | producer cadence measured |
| `phase_continuity_valid` | `PHASE_CONTINUITY_PROBE_0` | coherent I/Q across wrap |
| `source_bandwidth_known` | `APPLY_MEASURED_BANDWIDTH_<hz>_<factor>_CONFIRMED` | factor ∈ {1,2,4,8}; `occupied × factor ≤ rate`; literal word `CONFIRMED` |
| strategy = `BITSCRAMBLER_RING` | needs `bitscrambler_path_available` AND `sparse_factor_allowed ≥ 4` | see bench below; choose factor ≥ 4 in the APPLY command |
| `bitscrambler_path_available` | `BENCH_RING_PIPELINE_0_1000` pass | includes WBFM self-test + PARLIO bench input + ≥20 % synthetic margin |
| `s_mode0_soak_passed` | `PRODUCER_SOAK_0_30000` only | 30 s soak, zero overruns/missed words |

Failure reasons printed by `LIVE_START` map 1:1 onto the first failing row:
`SOURCE_RATE_UNKNOWN`, `PHASE_CONTINUITY_UNPROVEN`,
`ANTI_ALIAS_BANDWIDTH_UNKNOWN`, `MODE0_STAGED_SOAK_NOT_PASSED`,
`NO_MEASURED_CONSUMER_WITH_CPU_MARGIN`.

**Retuning resets the RF-dependent gates** (`SET`/`BW` clears
`measured_source_rate` and the soak flag): re-run this whole sequence after
any tune change.

## 3. Runbook

```
STATUS                          # link + device check
SET <band> <channel>            # tune to the VTX (resets gates if retuned)
PRODUCER_CADENCE_PROBE_0        # note reported samples/s
WRAP_FLAG_PROBE_0               # wrap behaviour of the 16K window
PHASE_CONTINUITY_PROBE_0        # must report continuity valid
APPLY_MEASURED_BANDWIDTH_<occupied_hz>_4_CONFIRMED
WBFM_HWTEST                     # discriminator self-test
BENCH_PARLIO                    # AV output bench
BENCH_RING_PIPELINE_0_1000      # ring under load; sets bitscrambler path
PRODUCER_SOAK_0_30000           # THE gate: 30 s continuous producer
LIVE_START                      # accepted only when every gate is green
CVBS_LOCK_STATUS                # H/V lock on recovered video
PIPELINE_STATS                  # stage timings, rates, underruns
LIVE_STOP                       # prints C5VRX_LIVE_RING_STATS
```

Optional diagnostics along the way:
`TONE_RESPONSE_PROBE_0_<offset_hz>_<rate>`, `FINE_TUNE_VERIFY_...`,
`USB_PREVIEW_START` (grayscale side tap), `BENCH_USB_PREVIEW`.

## 4. Continuous-RF definition-of-done checklist (from the soak)

Recorded from `C5VRX_LIVE_RING_STATS` at `LIVE_STOP` (and during soak):

- [ ] `wraps=` advanced by hundreds over the 30 s soak
- [ ] `overruns=0`
- [ ] `missed_words=0`
- [ ] `fatal_stops=0`, `fatal_reason=NONE`
- [ ] `producer_abs` monotonic; `consumer_abs` follows;
      `lag` small and stable; `lag_max ≈ lag`
- [ ] no finite-capture/retrigger activity while LIVE ran
- [ ] worst-case service below deadline (bench copy stats vs cadence)

Soak failure diagnosis — `fatal_reason=` tells exactly which gate tripped:

| fatal_reason | Meaning |
|---|---|
| `PRODUCER_STOPPED` | dump engine disabled itself mid-soak |
| `SERVICE_INTERVAL_AMBIGUOUS` | reader serviced too late: possible advance ≥ full ring |
| `READER_INSIDE_GUARD` | writer caught up to the guarded reader region |
| `COPY_AMBIGUOUS` | writer advanced ambiguously while copying a block |

## 5. After `LIVE START`: AV-out physical ladder (needs hardware bench)

Once the USB gates are green, issue #5 section 7 applies before "AV out"
may be claimed done:

1. `CVBS_TEST` (synthetic PAL/NTSC) into exactly one 75 Ω load on a scope:
   sync tip / blanking / black / white levels, H/V timing, vertical +
   interlace timing, field parity.
2. Stable lock on real goggles/monitor/DVR from synthetic output.
3. Switch to real RF: VTX → receiver → `LIVE START`; prove stable
   monochrome first.
4. Preserve burst/chroma through WBFM → conditioner → PARLIO; prove real
   color against a known-good receiver where practical.
5. Torture tests: hammer USB while AV runs; connect/disconnect USB
   repeatedly; power-cycle the VTX — AV stays legal and relocks without
   an ESP reboot.
6. Soak ≥ 60 minutes: `AV underruns = 0`, `AV fallback events = 0`,
   no latency growth, ring counters clean throughout.

Each step's numbers go into the session bundle (`c5vrx_lab.py`) so the
hardware evidence chain stays auditable.
