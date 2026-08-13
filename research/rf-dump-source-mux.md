# ESP32-C5 RF dump source mux

Status vocabulary in this document is literal: **PROVEN STATICALLY**, **LIKELY**,
**CANDIDATE**, and **UNKNOWN** are not interchangeable.

## `set_dump_mode()` exact reconstruction

The complete C5 v6.0.2 function is only 42 bytes and touches two registers:

```c
REG(0x600a08cc) &= 0xff87ffff;       /* clear bits 22:19 */
v = REG(0x600a70b8) & 0xfffffff8;   /* clear bits 2:0 */
if (mode == 0) v |= 1;
REG(0x600a70b8) = v;
```

An exhaustive disassembly of every `components/**/esp32c5/*.a` archive found
no other exact access to either address. Therefore the bit operations and the
absence of another statically visible accessor in the shipped C5 archives are
**PROVEN STATICALLY**; runtime/ROM ownership is not claimed.

What this proves semantically is narrower than the historical source comments:

- mode zero selects mux code 1;
- every nonzero integer selects mux code 0;
- codes 2..7 are cleared but never selected by this function;
- bits 22:19 at `0x600a08cc` are always cleared, independent of the argument;
- it contains no sample-width, I/Q-order, clock-divider or filter-dependent
  branch.

Historical Espressif tooling calls mode zero the normal/default or BB dump and
describes its alternate setup as FE/13-bit dump. That tooling targets older
register maps and explicitly performs more writes than C5 does. Accordingly:

| Claim | Status |
| --- | --- |
| `0x600a70b8[2:0]` is a dump-source mux | **LIKELY**, based on function role and one-of-eight field shape |
| code 1 is the existing packed 10+10 receive path | **LIKELY**, because all C5VRX captures call `set_dump_mode(0)` |
| code 0 is FE rather than BB | **CANDIDATE**, inherited from older tooling, not named by C5 machine code |
| code 0 changes width to 13+13 | **UNKNOWN**; C5 makes no width-dependent branch |
| either code changes I/Q order or decimation | **UNKNOWN**; no supporting write exists in this function |

The safe `DUMP MODE PROBE` therefore does not vary arbitrary `set_dump_mode`
values: all nonzero values are identical. It holds this mux in its established
mode-zero state while comparing producer modes 0, 11 and 12.

## Producer modes 11 and 12

Both are branches inside `adctrig`, not `set_dump_mode()` values.

| Register | Ordinary mode 0 | Mode 11 | Mode 12 | Static conclusion |
| --- | --- | --- | --- | --- |
| `0x600a9018[23:18]` | `0x1b` | `0x17` | `0x0b` | different dump packing/timing configuration; exact field name **UNKNOWN** |
| `0x600a9018[17:12]` | `0x1a` | `0x16` | `0x08` | different dump packing/timing configuration; exact field name **UNKNOWN** |
| `0x600a9018[11:6]` | `0x19` | `0x15` | `0x15` | modes 11/12 share this subfield |
| `0x600a9018[5:0]` | `0x18` | `0x14` | `0x14` | modes 11/12 share this subfield |
| `0x600a9008[24:17]` | OR register with `0x01e00000` (field value `0xf0`) | field `0x0b` | field `0x09` | mode/source selector, overlapping historical rate write |
| `0x600a20b4[0]` | clear | clear | set | mode 12 alone enables an alternate FE/BB path |
| `0x600a20ac[31:29]` | unchanged | unchanged | set to 2 | mode 12 alone chooses an alternate subpath |
| `0x600a9c04[21]` | unchanged after initial all-ones write | unchanged | clear | mode 12 changes an additional PHY control |

`0x600a20b4` and `0x600a20ac` lie in the same `0x600a20xx` block used heavily
by Bluetooth direction-finding/CTE IQ selection in `libbtbb.a`, although the
exact C5 addresses used by those helpers are mostly nearby rather than equal.
That makes an alternate radio/IQ diagnostic path **LIKELY** for mode 12. It
does not prove raw ADC, post-AGC, post-filter, FFT, or decimated IQ. Those
labels remain **UNKNOWN** until `DUMP MODE PROBE` measures format plausibility,
pointer cadence and signal response on silicon.

## Tuning interaction

The automatic-gain configure/start/stop blocks call no channel or RFPLL setter.
Ordinary mode 0 and mode 11 clear `0x600a20b4[0]`; mode 12 additionally changes
the alternate-path controls above. No direct RFPLL-frequency register readback
API is available. Firmware therefore records the strongest supported proxy:
the public Wi-Fi primary-channel and bandwidth before setup, after setup,
during/after probes and after teardown, plus whether `phy_set_freq()` was
requested. Preservation of the public readback is **PROVEN ON HARDWARE** only
after logs exist; preservation of a fine direct retune remains **UNKNOWN**.
