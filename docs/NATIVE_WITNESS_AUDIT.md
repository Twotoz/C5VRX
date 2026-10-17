# Native start/completion witness audit (2026-10-14)

C5VRX by Twotoz and the C5VRX contributors. This extends the project's
archived native experiments and the pinned Espressif PHY evidence. Source:
[C5VRX](https://github.com/Twotoz/C5VRX),
[official website and Discord invite](https://twotoz.github.io/C5VRX/).

No reliable acquisition-complete MMIO witness has been established by this
audit. An enable call, cleared force bit, changed tuple, or stable tuple is
not sufficient evidence that exactly one physical acquisition completed.

## A concrete difference in the enable precondition

The pinned C5 archive SHA256 is
`dbf33c418c8d408d4005c849d12a1432deea82e2e5e57de3c8ddf914d104fffb`.
`research/native_agc_evidence.py` was rerun in `espressif/idf:v6.0.2` on
2026-10-14 and verified that hash. Its `phy_enable_agc` disassembly shows:

1. `0x114..0x122`: clear `0x600a7030[29]`.
2. `0x124..0x12c`: read, set, write `0x600a702c[23]`.
3. `0x12e..0x138`: read, clear, write that same bit.

The current acquire preflight requires bit23 already set, because Direct
Gain owns a forced row. Therefore the set operation does not provide a
low-to-high transition. In contrast, the archived native hold/resume lab
explicitly refuses an initially set bit23 before using the vendor helper
(`legacy/c5vrx4/main/phy_rx_lab.c`, `phy_rx_lab_run_native_hold`). The
archived native restore also calls `phy_force_rx_gain(false, 0)`
(`legacy/c5vrx4/main/rf.c`, `analog_phy_restore_lock`).

The pinned `phy_force_rx_gain` body has no false-path branch. Instructions
`0x0..0x12` write `(old & 0x00ffffff) | (index << 24)` to702C; instructions
`0x14..0x28` write `(current & 0xff7fffff) | ((enable << 23) & 0x00800000)`
to the same word. Therefore `phy_force_rx_gain(false, previous)` preserves
the previous top byte when it already equals `previous`, clears bit23,
and preserves all other fields. This proves field semantics, not the
physical effect or duration of releasing force while BB is held.

The smallest isolated trigger hypothesis is to release force while BB is
still closed, verify bit23 low, then invoke the existing enable helper so
its set/clear sequence actually pulses from low. Start the guarded clock
**before the earliest bit23 clear**, including the release call, verification
and enable in the first observation interval. Keep the existing 2us maximum
gap and60us timeout; any timing failure must close and restore.

There is a substantial counterargument: the existing helper clears the BB
gate while force is still high, then clears force. Its final falling edge
may already provide the correct native release. Neither the vendor body nor
the legacy lab's refusal establishes an initially-low bit23 requirement.
Direct call-relocation inspection of the pinned archive finds enable callers
`phy_wakeup_init` (`0x278`), `phy_bb_init` (`0x230`), `phy_chip_set_chan`
(`0x158`) and `phy_chip_set_chan_offset` (`0x60`), but no direct
`phy_force_rx_gain` call relocations. The callers do not establish the
bit23 value at entry. There is no vendor-call proof that the present path
is wrong or the proposed path is correct.

This is a bounded A/B proposal, not a production fix or an extra permission
to open an unmeasured RF state. BB hold behavior must also be physically
checked across force release. No firmware or board state was changed by
this audit.

`phy_bb_fsm_rst` is a separate pulse of `0x600a7c28[1]`; the archive does
not validate it as an isolated native AGC trigger. Replaying initializers
or guessing a reset sequence is not justified by a missing start proxy.

## What the archived captures actually establish

`legacy/c5vrx4/docs/native-agc-v2.md` reports complete dump-word gain walks
roughly every 25–50us, with typical steps around 0.6us. The old7078 initial
gain re-entry proxy undercounted these by about an order of magnitude.
Register sweeps included walks around 1.8–3.4us. Consequently a maximum
2us interval between complete tuple observations can miss individual
steps and potentially a whole short walk. It remains a useful abort
contract, but does not prove a single acquisition count.

`legacy/c5vrx4/NATIVE_AGC_MASK.md` records the 2026-10-06 board calibration:
DIAG30 covered 57.5% of walk samples and no trapped samples; inverted
DIAG31 covered 95% of walks but also 20% of trapped samples. Neither result
establishes a universal completion edge, and these percentages cannot be
transferred to the current board context without new correlation.

The full-word layout is attributed to ESPARGOS esp-sdr: gain in[27:20],
state in[31:28]. The corresponding MODEM_DIAG lanes were tested bit-exact
at forcedG52/state1; a static test does not establish dynamic state meaning.
The archived acquisition mask calibration selects an empirical separator,
not a decoded hardware-complete bit.

## Required correlation before claiming one scan

A current tagged gain/state trace must cover the open pulse, closing edge
and held state, at enough temporal resolution to distinguish individual
gain steps. Correlate actual PBUS tuples with those tags, establish which
transition marks acquisition start and final settling, and count all walks
inside each pulse. Preserve the60us timeout,2us observation gap limit,
single PHY owner and verified forced gain/DC rollback while investigating.

`legacy/c5vrx3/tools/analyze_agc_words.py` is useful research tooling, with
limits: `events()` merges gain changes separated by at most4us, event
duration omits final settling, and its aggregate start intervals span
separate captured windows. Use individual contiguous-window evidence for
physical timing; the aggregate interval minimum cannot prove a retrigger
bound across disjoint windows.

The pending full7094/first-and-last7078/tuple-change diagnostic can
distinguish observations. Zero proxy matches does not establish no start;
zero tuple changes does not establish no scan; tuple stability does not
establish final settling. Retain `completed_scans=UNVERIFIED` until the
correlation above is available.
