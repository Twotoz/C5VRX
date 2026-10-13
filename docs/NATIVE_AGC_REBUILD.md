# Periodic native acquisition: rebuild evidence and ownership

C5VRX by Twotoz and contributors. Extends `legacy/c5vrx3/docs/native-agc-paced.md`,
legacy commit `5d50607` (`main/native_agc_pace.c`), and the subsequent
`legacy/c5vrx4/docs/native-agc-analog-patch.md` investigation.

The operator explicitly requests periodic native AGC in PR 212. This permits
implementation despite the default Direct Gain-only policy. It does not prove
better reception or authorize an unmerged-board flash.

## Findings that change the implementation

The old window-zero loop watches 7078 re-enter the initial gain and then 706C
hold still for 1 us, with a 60 us timeout. Later full-word captures showed
that this proxy substantially undercounts acquisitions. Its comment claiming
exactly one acquisition is stronger than its evidence. Copying that ISR would
also block interrupts for up to 60 us. Opens and successful acquisition evidence
must be reported separately.

Current root `rf.c` unconditionally disables BB AGC, destructively disables
RF AGC and forces gain. Merely clearing 7030 bit 29 cannot undo this. Native
enable clears that bit and pulses 702C bit 23, but does not restore 705C or the
cleared 284C fields. Legacy therefore selected native ownership before PHY
initialization and refused forced-gain writes throughout that boot.

Current DCO hold also owns PBUS gain words in debug mode. Native acquisition
requires leaving that hold first. Reapplying a DC row for an assumed gain can
freeze a different RF tuple. This must be an explicit gain/DC handoff, not an
independent AGC timer fighting the observer.

## Reproduced static evidence

Run:

```sh
docker run --rm -v "$PWD:/src:ro" espressif/idf:v6.0.2 \
  python /src/research/native_agc_evidence.py
```

The script refuses any libphy archive except SHA256
`dbf33c418c8d408d4005c849d12a1432deea82e2e5e57de3c8ddf914d104fffb`.
The 2026-10-13 run confirms the previously decoded AGC gates and shows that
`phy_pbus_set_rxgain` expands the calibrated packed tuple into these PBUS words:

| Component | Block, bank | Written value |
| --- | --- | --- |
| 5 GHz RF | 8, 1 | packed[20:12], 9 bits |
| BB | 0, 2 | packed[10:4], 7 bits |
| fine | 1, 2 | 0xd0 OR packed[2:0] |

`phy_pbus_rd` returns a 9-bit field from the address resolved by
`phy_pbus_rd_addr`; it does not enter debug mode. This supplies a candidate
nonintrusive live-tuple readback. It is not yet verified as a time-consistent
native gain read on the board. Compare repeated RF/BB/fine reads against known
forced rows before adopting any native result. Refuse ambiguous/unmatched
tuples rather than treating 702C's configured force index as actual gain.

## Implemented bounded bench controller (physical validation pending)

Console `~` toggles a RAM-only acquire/hold controller after startup. It is
OFF at every boot; Direct Gain remains the default. The nominal period is
20 ms, not 5 ms: every native pulse is a real analog transition even when it
lands on the same index, and the existing control guard requires 5 ms of
settled gain. A 5-ms period would continually exclude ordinary control windows.

The observer is the sole gain owner in this mode. Each attempt acquires the
PHY mutex, verifies the current forced tuple twice, leaves DCO debug hold,
restores only the exact RF-AGC fields saved before the first disable, and
opens BB AGC using the pinned vendor helper. Tasks are suspended for this
bounded window, while interrupts and the hardware video stream keep running.
There is no new timer ISR or interrupt-masking spin loop.

The loop retains the old initial-gain start proxy but replaces the old 706C
"gain" proxy with actual PBUS RF/BB/fine tuples. It closes after a candidate
tuple holds for 2 us, or at a 60-us timeout / >2-us sampling gap / 512 polls.
The inverse table search runs after BB closes. Two further equal reads must
match the candidate and exactly one calibrated table row. Missing, malformed,
ambiguous and unsupported tuples are refused. The hold is then a forced
calibrated gain with its own DCO row, and software gain/epoch/time are updated.

Every changed-hardware exit restores disabled RF/BB AGC and forced gain/DC;
register and tuple readback check that restoration. A failed check rolls back
once to the previous row. Failed restoration pauses the gain owner and reports
`FAULT_PAUSED`; other faults return to Direct Gain. Existing overload evidence
also hands control back to Direct Gain immediately. Retune/calibration/context
changes disable the experiment. Direct Gain writes and mode switches share
PHY ownership so task preemption cannot make the two controllers fight.

The saved vendor setup is restricted to the initial 5865-MHz, zero-offset,
BW40 context. Other contexts refuse; no snapshot from a different channel is
silently used. Support for arbitrary native contexts remains unfinished.
Restoring these saved fields is a bench hypothesis needing physical A/B,
not a claim of universally reversible native initialization.

`NATIVE_AGC` reports attempts, landing candidates, faults and measured maximum
open duration separately, with `completed_scans=UNVERIFIED`. The nominal
60-us timeout is not a hard wall-time guarantee through interrupt preemption.
No physical acquisition-complete flag or exactly-one-scan proof is claimed.
Host tests cover all 84 table rows and malformed/ambiguous tuples, start,
stable landing, reset, invalid evidence, missed polling and timeout. They do
not validate RF register restoration or improvement in picture/range.

## Remaining acceptance

Prefer exclusive native acquire/hold ownership to running two gain controllers.
Compare a boot-selected native owner (retains vendor RF setup) with a bounded
temporary handoff that saves the exact affected vendor fields before Direct
Gain disables them. The latter needs evidence of reversible RF restoration;
plain `phy_enable_agc` is insufficient. Choose only after examining retunes,
startup calibration and DCO restoration.

Use a bounded acquisition witness and timeout, preserve DMA streaming, reject
old-context measurements, and return safely to a known owner on failure.
Do not add a long spinning ISR simply to make a nominal interval precise.
Physical validation must establish native gain landing, true acquisition
count, held-gain stability, overload recovery and picture quality on the
current ultrafine lane. The old coarse-lane target is not automatically suitable.

Before broader use, measure gate restoration, PBUS agreement with captured
gain metadata, true acquisition count, held DC/IQ, overload/weak-signal recovery,
sampling gaps, CPU load and actual picture against Direct Gain. A host-passing
landing candidate is not sufficient to call the requested one scan complete.
