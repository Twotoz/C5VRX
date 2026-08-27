# ESP32-C5 native RF/IQ ring probe

Status: **POINTER RING PROVEN / EXACT VENDOR MODE-6 FIX PENDING PHYSICAL TEST**. The C5
physically demonstrated a single-enable, zero-trigger, zero-rearm circular
pointer lifecycle. This is not yet called true continuous IQ capture: the
first RAM-generation check did not prove that fixed locations were refreshed.
The PR21 LP-rearmed producer remains the fallback/reference implementation.

## First physical result: 80 M pointer steps/s

Repeated 500 ms runs on ESP32-C5 revision 1.0 produced approximately
40,000,000 absolute pointer steps and 2,441 strict high-to-low wraps:

```text
observations=202871 pointer_changes=202871 wraps=2441
min_ptr=0 max_ptr=16383 absolute_writer_samples=39999930
enable_assertions=1 enable_low=0 mode_low=0
software_rearms=0 software_triggers=0 terminal_done=0
writer_stopped_after_done=0 start_ctrl=80024000 final_ctrl=80024000
```

That is 79,999,860 pointer positions per second, within rounding of 80 MHz.
It proves that bit 17 changes the C5 from the finite `DONE` lifecycle into a
self-wrapping address-generator lifecycle. It is materially different from
PR18/PR21, whose effective rate included every 16,384-word terminal/rearm gap.

It does **not** yet prove 80 million fresh IQ words per second. The original
`content_changes` metric compared different addresses and was therefore not a
generation test. More importantly, the one fixed address sampled on each wrap
reported zero changes. The result can still be either a live writer that is
not visible through the LP SRAM view or an address generator traversing stale
RAM. The classification correctly remained `NATIVE_RING_REJECTED`.

The first implementation combined bit 17 with the mode-0 selector
`0x01e00000`. The unbounded AV run proved that hybrid was not permanent: it
asserted terminal status and stopped after 3,069 wraps (about 0.63 seconds),
then safely restored PAL. It also retained the same fixed-memory signature.

Reinspection of the pinned C5 machine code found the missing half of the
vendor operation. The only C5 branch that asserts bit 17 is trigger mode 6;
that branch also clears `0x600a9008[20:17]` with the exact complement mask
`0xffe1ffff` and selects `0x00080000`. Its preceding historical sample
argument of one clears `[23:21]`. Family tooling calls trigger mode 6 TX-end.
The corrected RX-only experiment now reproduces that complete C5 branch, so a
TX-end event should not occur accidentally. This remains a hypothesis until
the new unbounded physical run survives and shows refreshed memory.

The probe hashes a fixed set of locations once per observed generation and
reports `fixed_epoch_observations`, `fixed_epoch_changes`,
`pointer_ring_pass`, and `memory_ring_pass` separately. Only the latter may
establish continuously refreshed memory.

## Hypothesis and evidence boundary

Historical Espressif RF-test tooling describes `dump_trig=1` as “dump first,
then trigger”, reports a current pointer and wrap flag, and reconstructs data
across a circular buffer. Historical register descriptions assign bit 17 to
`DUMP_TRIG_MODE`, bit 18 to status/done, bit 19 to software start and bit 31 to
enable. The ESP32-C5 controller recovered from ESP-IDF v6.0.2 uses those same
four control-bit positions.

That family resemblance justifies one tightly scoped experiment: set C5
control bit 17, assert enable once and observe without a software trigger. It
does **not** establish the semantics on C5. No historical ESP32 register address
or `TOADCDUMP_LOOPEN` write is copied into this implementation.

## Exact C5 `adctrig()` finding

The audited archive is:

```text
ESP-IDF: librftest.a from v6.0.2, esp32c5
SHA-256: 0f9680b41612762d854accf2334412e3e01b206a3ec290bbbb232842fdaec7ba
symbol:  adctrig, size 0x57e
```

The function retains the historic nine-slot calling shape, but the historic
fifth argument is ignored. At entry, C5 saves `a5` (argument six), `a1`, `a2`
and the ninth stack argument. At offset `0x26`, it overwrites `a4` with
`a3 >> 1` before any read or save of the incoming `a4` value:

```text
0x0a  mv    s1,a5
0x20  mv    s0,a1
0x22  mv    s2,a2
0x24  lw    s3,64(sp)
0x26  srai  a4,a3,1
```

Consequently, calling C5 `adctrig(..., dump_trig=1, ...)` does not select
pre-trigger mode. This is a high-confidence machine-code result and corrects
the earlier assumption that the fifth ABI slot still controlled bit 17.

Bit 17 is nevertheless written by C5 vendor code in the recovered trigger-mode
6 branch at `0x442..0x44e`: it reads `0x600a9004`, ORs `0x00020000`, and writes
the register back. That is also high-confidence machine-code evidence. Calling
it the historical dump-first/self-wrap control remains only a medium-confidence
family-derived hypothesis until physical C5 behavior confirms it.

Confidence summary:

| Finding | Confidence |
|---|---|
| Nine argument ABI shape remains | High / direct disassembly |
| Incoming fifth argument is unused | High / direct disassembly |
| Trigger-mode 6 sets control bit 17 | High / direct disassembly |
| Bit 17 means dump-first on C5 | Medium / family evidence only |
| Bit 17 self-wraps indefinitely | Unknown until this probe |

`tools/audit_native_ring_probe.py` checks these facts against the pinned archive
and checks that the experimental LP path contains no trigger/rearm write.

## Implementation

`C5VRX_RF_DUMP_MODE_NATIVE_RING` reuses only the audited ordinary 5 GHz receive
source setup, then applies the single bit-17 hypothesis. The explicit probe:

1. snapshots the same controller/FE/ownership state as the existing producer;
2. transfers the fixed 64 KiB RAM window to the RF writer;
3. asserts bit 17 and `ENABLE` once;
4. never pulses bit 19;
5. never lowers/raises enable to rearm;
6. observes from the LP core at a tight cadence for a bounded 10–2000 ms;
7. disables once, restores ownership/registers and returns to normal firmware.

The HP core waits only for the bounded acceptance command. There is no
per-sample HP processing and no CPU-copy software ring. Sparse LP RAM reads are
telemetry only; USB carries control/status, never realtime IQ or video.

The LP observer reports:

- physical pointer, 64-bit absolute position and high-to-low wrap count;
- observation/pointer-change counts and pointer range;
- enable/mode loss and trigger-bit observations;
- terminal/done observations and progress after done;
- software-trigger and software-rearm counters (both must remain zero);
- sparse content changes, changes across generations, IQ power and signature;
- ambiguous backward samples and stopped-after-done detection;
- phase residual across `16382, 16383, 0, 1` for a coherent tone;
- numeric fault reason and final classification.

Backward pointer samples count as a wrap only when the prior value is at least
`0x3000` and the new value is at most `0x0fff`. Other regressions are exposed as
ambiguous and fail acceptance. This reduces stale cross-domain reads being
misreported as hardware generations. `absolute_writer_samples` is reported as
`wraps * 16384ULL + physical_pointer`; acceptance rejects ambiguous observations.

## Physical acceptance procedure

Build/flash `sdkconfig.defaults.xiao_native_ring_probe`. Keep the complete
serial log. Each command is bounded; 500 ms is a useful first duration.

1. VTX off:

   ```text
   NATIVE RING PROBE OFF 500
   ```

2. Turn on the known A1/5865 MHz VTX without resetting the C5:

   ```text
   NATIVE RING PROBE ON 500
   ```

3. Replace/modulate the source with a coherent tone suitable for boundary
   phase analysis, then run:

   ```text
   NATIVE RING PROBE TONE 500
   ```

Every structural run requires one enable assertion, zero software triggers,
zero software rearms, at least four strict physical wraps, continuing pointer
and RAM changes, no terminal stop, and full low/high pointer coverage. The ON
run must differ from the stored OFF power/content baseline. Only the ordered
OFF → ON → TONE sequence can emit `classification=NATIVE_RING_PROVEN`, and the
TONE run additionally requires bounded phase residuals across hardware wraps.

Possible classifications:

- `NATIVE_RING_PROVEN`: all structural, RF and coherent-boundary gates passed;
- `NATIVE_RING_REJECTED`: a structural hardware gate failed;
- `INCONCLUSIVE`: the ring structure ran, but OFF/ON/tone proof is incomplete.

An immediate stop at pointer 16383, asserted DONE without later progress,
unchanging contents, enable loss or any nonzero trigger/rearm counter is a
negative result. Register snapshots and all counters remain in the output so a
negative result is useful rather than being silently converted to the PR21
path.

## Experimental AV acceptance route

The dedicated native-ring test profile now contains a deliberately guarded
hardware-consumer experiment. After three seconds of PAL boot diagnostics it
connects:

```text
bit-17 64 KiB pointer-ring hypothesis
  -> circular AHB-GDMA read
  -> 4:1 BitScrambler WBFM
  -> 20 MS/s PARLIO
  -> resistor DAC / analog output
```

The 20 MS/s output clock follows from the physically observed 80 M pointer
steps/s and the existing four-input-word BitScrambler program. The RF enable is
asserted once; the native path contains no software trigger and no rearm. The
LP core acquires an 8,192-word lead, starts PARLIO, observes the GDMA consumer,
and owns failure shutdown. The HP core remains parked because the RF engine
owns the SRAM window, so USB is boot diagnostics only after the handoff.

This route is an acceptance experiment, not a declaration that continuous IQ
already works. Its startup line therefore reports `pointer_rate_hz=80000000`
and `iq_freshness=PHYSICAL_AV_PENDING`. Usable RF-dependent analog video, fixed
generation changes, and boundary phase continuity are still required before a
production `c5vrx_iq_stream` backend replaces PR21's stitched fallback.

REGDMA is intentionally not used. On ESP32-C5 it is the power-management
register backup/restore mechanism (including modem/PHY retention), not a bulk
80 MS/s SRAM transport. The relevant dataplane engine is the existing circular
AHB-GDMA consumer.

## Integration gate

There is deliberately no production native-ring `c5vrx_iq_stream` backend yet.
The AV switch is restricted to the dedicated experimental acceptance profile.
If physical hardware emits `NATIVE_RING_PROVEN`, the
next change may place it behind a producer-neutral `c5vrx_iq_stream` interface
and feed the existing circular GDMA → BitScrambler WBFM → PARLIO path. If it is
rejected, PR21’s stitched 16,384-word LP-rearm architecture remains active and
must continue to be described as stitched, not native hardware-circular IQ.

## Correction to the old probe

The former `RING_PROBE` used the RX-error trigger but still invoked the C5
wrapper with the historical fifth argument set to zero. Because C5 ignores
that argument, and because the test did not explicitly assert bit 17 while
withholding the software trigger, it tested the finite/post-trigger lifecycle.
Its negative result does not rule out this bit-17 pre-trigger hypothesis.
