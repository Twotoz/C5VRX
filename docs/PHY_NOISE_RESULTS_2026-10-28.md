# C5 PHY noise experiments, 2026-10-28

C5VRX by Twotoz and the C5VRX contributors. Extends the pinned-PHY work in
PR #254 and the current sensitivity lab. [Source](https://github.com/Twotoz/C5VRX);
[official website and Discord invite](https://twotoz.github.io/C5VRX/).

## Scope and interpretation

ESP32-C5 rev1.0, A1 5865 MHz, U85Q, operator VTX **off**. The antenna/input
termination and ambient RF were not characterized. Each candidate uses
STOCK-TEST-TEST-STOCK, 16 raw 8190-byte captures per stage, 300 ms dwell,
one screened physical gain tuple and held calibrated DC pair. Negative
deltas below mean less DC-removed captured IQ power in cells squared.
They are **not** calibrated input noise figure, C/N or sensitivity changes.

The approximately 6 dB NF mentioned in discussion remains an estimate.
Changing conversion gain, bandwidth, IQ correction or quantization can
change captured noise without improving the desired signal. More actual
analog bias current could help an LNA, mixer or another stage, but neither
a symbol name nor a high argument proves that a current changed.

Pinned `espressif/idf:v6.0.2` C5 `libphy.a` SHA-256:
`dbf33c418c8d408d4005c849d12a1432deea82e2e5e57de3c8ddf914d104fffb`.
Official Espressif ROM package `esp-rom-elfs` release `20260528` was also
inspected. Package SHA-256:
`caa463d3cbef2430a5a35847c1d9f2f152403b17a802050927ff60c8da54fe46`;
C5 rev100 ROM ELF SHA-256:
`49693d745eb4b4e6e6c77be4f5213765d545d8c7a1d9742a8a619bf596c4e27a`.

The per-profile results, complete-run markers, entry context and original
console SHA-256 hashes are in
[`research/data/phy_noise_lab_2026-10-28.json`](../research/data/phy_noise_lab_2026-10-28.json).
Original console logs remain in the local experiment directories identified
by the dataset. A console hash establishes identity, not independent RF calibration.

## Completed first runs

| Run | Result |
| --- | --- |
| Noise shams `b` | 8 complete controls; RMS paired delta 0.0254 dB, range -0.0213..+0.0470 dB |
| Existing noise sweep `c` | 90 profiles; 8 rejected entries, 1 IQ-breaking setting, 3 skips; final restoration verified |
| Targeted bias `d` | 3 candidates and 4 shams, all complete; mean candidate delta -0.04965 dB |
| Current-level `f` | 20 levels and 21 shams, all complete; mean candidate delta +0.00178 dB; final restoration verified |
| Current confirmation `i` | 8 candidates and 9 shams, all complete; four 255 trials mean +0.00822 dB; no reproduced reduction |
| Loop cap `g` | 7 settings (current bin and calibration bins 0..6) and 8 shams, all complete; +0.019..+0.056 dB against shams -0.064..+0.044; no reduction |
| Spur slots `h` | slot 0 +-512: +0.003 / -0.012 dB, no effect; slot 1 skipped (already active); slots 2..3 rejected (incomplete), untested |

Existing sweep observations: DFILT mode 4 gave -0.5105 dB and mode 8
-0.4005 dB against stock mode 12; a mode-12 no-op gave -0.0375 dB.
These are transfer/bandwidth comparisons, not proof of lower NF. Disabling
RX IQ-correction bits increased captured noise by approximately
0.45..0.93 dB; signal transfer was not measured, so this alone does not
rank analog video quality. NF-auto patterns 0..7 showed no clear effect;
8..15 did not latch. `WIFI_ENABLE_ARG0` broke IQ and was restored.

### The apparent -5.4 dB result was invalid

The first sweep's firmware printed a numeric noise delta even when a
rejected entry or broken IQ left TEST stages empty. Zero empty-test means
minus approximately 5.4 dB STOCK produced a false -5.4 dB "improvement".
Those measurements have no valid comparison. The Python analysis excludes
them. Firmware now prints `nan` with `incomplete_measurement` unless all
four stages completed with PASS status. No 5.4 dB NF gain was established.

## Bias comparison

`phy_bias_reg_set()` consumes no argument. It initializes analog block
0x6a/host 1/registers 0 and 1 to 0x9f/0x7f. The board's operating readback
was 0xcf/0x7f. Suite `d` compares only register-0 bits 7:4, 0xc against
0x9; the low nibble and register 1 stay unchanged. Other entry bytes skip.

| Trial | Noise delta, dB | Standard error, dB |
| --- | ---: | ---: |
| 1 | -0.04214 | 0.03158 |
| 2 | -0.08394 | 0.02205 |
| 3 | -0.02286 | 0.02918 |

Mean -0.04965 dB; interleaved sham RMS 0.01656 dB. This is a small raw
noise difference between two witnessed register states. The field is not
established as LNA current. Suite `e` exists for a subsequent fixed-carrier
comparison; no such carrier result is claimed here.

## Current-level sweep

The pinned `phy_current_level_set` stores the low argument byte at
`phy_param+41`, then returns. The linked firmware reduces this to `sb; ret`.
There is no direct hardware write in the setter. No active consumer was
established in the archive search or inspected ROM paths; indirect or
deferred effects are not conclusively excluded. The linked ECO3 setup
does pass the actual `phy_param` pointer to ROM, so merely sharing the
parameter block does not establish which byte ROM consumes.

Suite `f` calls the real setter and verifies the byte before and after
captures; rollback also calls the setter with the exact saved byte.
Stock byte was 0. Values are raw byte patterns, not documented milliamps.

| Value | Paired noise delta, dB | Value | Paired noise delta, dB |
| ---: | ---: | ---: | ---: |
| 0 | +0.0313 | 10 | -0.0266 |
| 1 | +0.0323 | 11 | -0.0056 |
| 2 | +0.0251 | 12 | +0.0325 |
| 3 | +0.0085 | 13 | +0.0225 |
| 4 | -0.0198 | 14 | +0.0074 |
| 5 | +0.0321 | 15 | -0.0180 |
| 6 | -0.0147 | 31 | -0.0463 |
| 7 | +0.0083 | 63 | +0.0176 |
| 8 | +0.0318 | 127 | -0.0108 |
| 9 | -0.0085 | 255 | -0.0635 |

Individual standard errors were about 0.02..0.03 dB. The 21 interleaved
shams had RMS delta 0.02838 dB and mean +0.00129 dB. Their STOCK baseline
fell from 5.461 to 5.354 dB cells squared over time. That common drift is
real in this log, but it does not identify a cause. Values were tested in
ascending order, so time and value are confounded; the paired deltas do
not decrease monotonically with value.

255 gave the largest single reduction (-0.06350 dB, SE 0.02326 dB).
This merits confirmation, not automatic permanent selection. Suite `i`
repeats 255 four times in the counterbalanced order
`255,15,255,0,0,255,15,255`, each with adjacent STOCK stages and shams.
The confirmation completed with 17 profiles, no rejections/broken IQ/skips,
and verified final restoration:

| Setting | Repeated deltas, dB | Mean, dB |
| --- | --- | ---: |
| 255 | -0.02387, +0.01231, +0.03662, +0.00780 | +0.00822 |
| 15 | -0.03101, +0.01710 | -0.00695 |
| 0 | -0.00233, -0.02985 | -0.01609 |

Sham mean was +0.00535 dB. The -0.0635 dB screening result for 255 did not
reproduce. These data do not support making 255 a production setting or
claiming a current-induced noise reduction under this receive configuration.

## Recovered loop-cap footprint and experiment

`phy_rx_loop_cap_set` takes a frequency in MHz and is called by
`phy_set_loopback_gain`. It constructs TX capacitor settings for internal
loopback calibration; an effect on live antenna RX remains a hypothesis.
The linked call path was traced through the archive, not substituted ROM.

For 5 GHz (`phy_param[42] != 0`), the persistent state is:

| State | Vendor-derived TEST value |
| --- | --- |
| `phy_param[690]` | 8 |
| PBUS block 9/bank 2, nine bits | `(cap[0] << 4) \| 8` |
| PBUS block 10/bank 2, nine bits | adjusted `cap[1]`, saturated to 1..14 |
| `0x600a0884` | PBUS transaction selector and strobe fields |

Calibration pairs start at `phy_param[0x2d2]`, seven pairs. Frequency-bin
upper boundaries are 5240, 5320, 5560, 5640, 5755, 5835 MHz, then the last
bin. Second-byte adjustments are `252,253,4,4,4,4,4`, with **uint8 wrap
before unsigned clamp**, matching the archive. They are lookup boundaries,
not independently validated frequency limits.

Suite `g` requires an existing idle PBUS debug state and verified calibrated
DC hold. It reproduces the footprint with 1 ms bounded transactions, saves
both full nine-bit words, cache byte and command word, and verifies exact
rollback plus physical gain/DC/frequency. The first candidate uses the
current frequency; subsequent cross-bin settings are explicitly separate
calibration-pair experiments without retuning.

**Board result** (suite `g`, `loopcap-targeted-20261028-01`, VTX off, 5865 MHz,
G79, 17 profiles complete, none rejected, rollback verified): the current
bin 6 +0.054 dB and calibration bins 0..6 +0.019..+0.056 dB of noise against
shams -0.064..+0.044 dB. No setting lowered the noise; all within the sham
spread.

## Spur slots: evidence and bounded experiment

The archive and rev100 ROM callers of `phy_spur_reg_write` pass index 0.
Arithmetic defines four coefficient-pair destinations before a shared word;
there is no independent silicon proof for slots 1..3. Missing vendor usage
does not itself establish danger, so bounded bench comparisons are valid
experiments provided their inferred mapping stays explicit.

For candidate index `s` in 0..3:

- Control: `0x600a7c14 + 4*s`.
- Coefficient pair: `0x600a7810 + 8*s`, then the next word.
- Shared coarse field: seven bits at shift `7*s` in `0x600a7830`.

The reconstructed ABI is two integer arguments (index, signed frequency
code); no recovered public prototype establishes the original typedefs.
Bounded coefficient-generating codes are -1967..-1 and 1..1967. Code zero
clears coefficients and disables: it is **not** restoration. The archive
replaces control bits 15:14 as well; rev100 ROM preserves them.

Suite `h` uses a direct masked-equivalent writer preserving those bits.
Nominal +/-5 MHz uses codes +/-512 under vendor scaling: coefficient words
`0x2febfafe`, `0x0865dbae`, coarse codes `0x10`/`0x70`, control low-14-bit
values `0x2200`/`0x3e00`. Four complete words are saved, read back and
restored; only the selected coarse field changes. Coefficients are installed
before enable. Cleanup disables first, restores coefficients/shared state,
then restores original control last. Apparent active entries skip. A physical
gain/DC mismatch after rollback reboots rather than resuming uncertain state.

Slot 0 is the reference arm; 1..3 are labeled `inferred_mapping`. Readback
proves stored bits, not silicon semantics. A null result cannot establish
whether a slot is absent or whether its filter is outside MODEM_DIAG.
A reduction could simply be removed bandwidth, including wanted FM content.
These controls are not assumed to cancel broadband thermal noise.

**Board result** (suite `h`, `spur-targeted-20261028-01`, VTX off, 5865 MHz,
G79, 13 profiles, rollback verified): slot 0 reference +512 +0.003 dB and
-512 -0.012 dB against shams -0.015..+0.022 dB, no effect (the no-carrier
spectra show no in-band spur to remove). Slot 1 was skipped: its readback
already looked active. Slots 2 and 3 (+-512) were all REJECTED_ENTRY with
incomplete measurements, so they remain untested.

## Validation and production policy

Host tests execute extracted production suite/transaction logic with mocked
SDK boundaries: exact byte restoration for all 256 possible stock current
bytes, shams/order/cancellation, dropped writes, capture failures, complete
PBUS/cache/command rollback, cap wrap and DC guards, and four-word spur
rollback with preserved fields. The pinned-IDF firmware builds. Flashed
builds passed image identity/heap/transport checks on COM10.

These are console-only bench controls. No experiment is written to NVS or
applied at normal boot. Absolute sensitivity, equal wanted-signal transfer,
useful video bandwidth and weak-carrier quality remain unmeasured here.
