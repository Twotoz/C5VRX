# C5 PHY bias, current, loop-cap and spur code audit

C5VRX by Twotoz and the C5VRX contributors, 2026-10-28.
Initial static analysis of Espressif's binary library. The later linked-code
audit, bounded experiment footprints and actual board results are recorded
in [PHY_NOISE_RESULTS_2026-10-28.md](PHY_NOISE_RESULTS_2026-10-28.md).

## Reproduction and scope

Image: `espressif/idf:v6.0.2`. Library:
`$IDF_PATH/components/esp_phy/lib/esp32c5/libphy.a`, SHA-256
`dbf33c418c8d408d4005c849d12a1432deea82e2e5e57de3c8ddf914d104fffb`.
Use the image's `riscv32-esp-elf-objdump -dr` on that archive. Section names
and instruction offsets below identify the evidence without depending on
disassembly line numbers. Also inspected all C5 archives in that directory
and the IDF C5 ROM linker definitions.

The public symbol names are clues, not calibrated electrical units. No
absolute noise-figure improvement or legal input range follows from them.

## phy_current_level_set

The entire function is a store of the low byte of argument `a0` into
`phy_param + 0x29`, then return (store instruction at +0x4).
It does not directly write an analog register.

No direct relocation-based reader of `phy_param+0x29`, or direct call to
the setter, was found in the inspected C5 archives. This is a limited
negative result: base-pointer arithmetic, indirect calls and ROM consumers
are not ruled out. In particular `phy_get_romfunc_addr` has an ECO-version-3
branch calling ROM `phy_param_addr` (+0x26) and `phy_get_romfuncs` (+0x2e).
That branch is not evidence that this board revision takes it or that ROM
reads byte 41. The actual consumer, valid values, application timing and
whether the setting affects RX at all remain unresolved.

Next probe: establish the exact linked path for the board revision and
trace parameter reads in its ROM/indirect consumers. Capture stock byte 41
and identify vendor-supplied values before designing a sweep. Do not treat
255 as a maximum physical current or a live setter as an immediate change.

## phy_bias_reg_set

No input argument is consumed. Four calls to `phy_i2c_writeReg_Mask` write
analog block 0x6a, host 1:

| Register | Bits | Value | Call offset |
| --- | --- | --- | --- |
| 0 | 3:0 | 15 | +0x12 |
| 1 | 7:4 | 7 | +0x28 |
| 0 | 7:4 | 9 | +0x3e |
| 1 | 3:0 | 15 | +0x58 |

Final bytes are 0x9f and 0x7f. `phy_rf_init` calls it at +0x74.
This confirms a fixed initialization helper, not an argument-controlled
current ramp. The previous PR254 audit witnessed operating bytes 0xcf/0x7f
and frequency calibration owning register 0's high nibble. Those operating
values were not remeasured here. Neither nibble's electrical function or
monotonic relationship to RX noise has been established.

Next probe: trace all writers and frequency-calibration ownership of these
fields, establish vendor-used values, then compare bounded candidates with
exact readback/restoration and equal signal transfer. Calling this reset
helper during reception is not yet a justified experiment.

## phy_rx_loop_cap_set

The incoming argument is passed to `phy_get_chan_cap`,
`phy_freq_to_mbgain` and `phy_freq_to_index`. `phy_set_loopback_gain`
calls it with the halfword at `phy_param+0x120` (+0x11c), consistent with
a frequency argument, not a capacitance/current level.

It constructs channel-dependent cap data, conditionally modifies it using
`phy_param[42]`, and calls `phy_txcap_setting` at +0x70.
That downstream function uses PBUS force calls for the nonzero-flag path
(addresses 9 and 10, second argument 2), or analog block 0x6b/host 1/reg 2
for the other path. These are coupled writes, not one isolated RX trim.

Inference: the observed caller is internal loopback/calibration plumbing.
An antenna-input receive-sensitivity effect has not been demonstrated.
Trace that cap state into ordinary RX before testing it as an RX knob.

## Spur slots

`phy_spur_reg_write(a0, a1)` uses the first argument as an index. Its
instruction arithmetic gives control address `0x600a7c14 + 4*index` and
coefficient pair base `0x600a7810 + 8*index`. It writes the low 16 control
bits, explicitly sets bit 13 according to whether the second argument is
nonzero, clears the coefficient pair, and fills coefficients using lookup
tables and signed arithmetic derived from that argument. This is a
frequency-dependent correction, not an amplification setting.

The inspected calls in `phy_spur_coef_cfg` explicitly pass index 0
(+0x6c before call +0x6e; +0xba before tail call +0xbe). The routine also
directly clears slot-0 enable bit 13. Address arithmetic alone does not
prove indices 1..3 are implemented independent hardware slots, nor establish
their bounds, coefficient format or position relative to MODEM_DIAG.

Next probe: recover hardware slot layout and routing, read controls and
coefficient pairs without changing them, and identify a repeatable narrow
spur in raw IQ. Only then test a targeted coefficient with saved-state
restore. A useful result must suppress that spur without removing FM
content. These controls cannot be assumed to cancel broadband thermal noise.

## Outcome

The analysis corrects the earlier claim that these names establish LNA
bias-current controls. It also corrects the opposite claim that a buffered
setter cannot affect current: its later consumer remains unknown.
At the time of this initial audit, no board writes or measurements had been
made. Subsequent bias/current board comparisons and the complete loop-cap
and spur experiment footprints are in `PHY_NOISE_RESULTS_2026-10-28.md`;
its status supersedes the "next probe" notes above. No calibrated NF or
sensitivity improvement has been established.
