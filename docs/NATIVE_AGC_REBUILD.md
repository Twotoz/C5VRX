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

## Implementation decision still to validate

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

No periodic native controller has been enabled by these research changes.
