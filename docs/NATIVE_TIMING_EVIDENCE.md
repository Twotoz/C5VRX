# Native acquisition timing after the PBUS-prefix fix

C5VRX by Twotoz and the C5VRX contributors. This extends the pinned PHY,
archived full-word native acquisition captures and the current board bench.

The `f3e6931` A1/5865-MHz attempt passed preflight, enabled native acquisition,
then returned `result_3` (`NATIVE_GAP`) with `open_us=8`. Subsequent status
remained live at OUTPUT 2.0x and Direct Gain regained ownership. Log:
`research/data/native_refusal_2026-10-14/a1-native-f3e6931.log`. This is a real opening followed by
an observation-gap abort, not a successful native landing or completed scan.

The pure acquisition state starts with `last_us=0`. Its first observation is
subject to the same maximum 2-us gap as every later one. The loop's elapsed
time begins before `phy_enable_agc`, then includes three PBUS getter calls,
the clock read and any interrupt delay before that observation. Consequently
the abort could occur before even processing the initial-gain proxy. The
existing log does not identify which poll failed or allocate those eight
microseconds among enabling, reading, interrupts and closing.

The pinned `phy_enable_agc` body is straight-line IRAM code: clear
0x600a7030 bit 29, set 0x600a702c bit 23, then clear that bit. It has no delay
or nested calls. That instruction structure does not establish wall time
under actual MMIO, CPU clock, cache and interrupt conditions.

Useful next instrumentation, with only bounded arithmetic/reads while open:

- Timestamp immediately before enable and immediately after its return.
- First complete tuple-observation time, number of polls and first gap's
  zero-based poll index.
- Maximum interval between complete observations, maximum tuple-read work
  time, invalid-tuple count, tuple changes and whether the start proxy was
  ever seen. Preserve the actual deadline and gap-guard decision.
- Print the collected scalars only after BB/RF close and verified forced
  gain/DC restoration. No console I/O or table search while native runs.

Do not move the first timestamp past enable merely to pass: that would hide
an unobserved interval in which hardware may already acquire. Do not increase
the gap allowance without evidence of what transitions could then be missed.

The three PBUS fields have already been established by the pinned read helper
and its jump-table relocations: RF is `(REG(0x600a08a8)>>9)&511`, BB is
`REG(0x600a0894)&511`, and fine is `(REG(0x600a0898)>>9)&511`. If measurements
show getter overhead, an inline read of these exact fields removes three
getter/address-switch calls without changing the fields, prefix checks or
gap limit. This is a justified optimization candidate, not a measured speedup.

The archived full-word captures found acquisitions around 1.8–3.4 us and
showed that the 7078 re-entry proxy undercounts restarts. A 2-us observation
gap limit plus a settled tuple is therefore not, by itself, proof of exactly
one acquisition. Current firmware must continue reporting candidate landings
separately from `completed_scans=UNVERIFIED`. A current high-resolution tagged
gain/state capture or a separately validated hardware completion witness is
needed before claiming exactly one physical scan per pulse.
