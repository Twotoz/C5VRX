# Remaining range evidence, 2026-10-13

C5VRX by Twotoz and the C5VRX contributors. This review extends the existing
range audit, recorded walks, calibration work and pinned PHY investigation.

## What gradual black video currently establishes

The operator reports black/monitor No Signal after gradually weakening a VTX,
while a cold boot without a VTX displays snow. These two observations can come
from different display acquisition states even with a continuously running IQ
stream. They do not establish Wi-Fi desensitization or stopped DMA. The menu
recovery in issue 213 changes both composite-sync source and transport state;
its success cannot choose between those explanations.

Archived `research/data/walk_2026-10-13/weak_00.bin` through `weak_05.bin`
contain 84, 122, 127, 114, 118 and 112 distinct IQ byte codes respectively.
Their largest-code fractions are 36, 25, 22, 26, 25 and 28 per mille. Those
particular captured weak windows therefore were not a constant/stopped IQ
value. The earlier VTX-off windows contain only 5–6 distinct codes with a
dominant fraction of 526–784 per mille and about I=-2, Q=+2.4–2.8 cells:
they are affected by the older DC/gain problem. These captures predate the
current fixes and do not prove the cause of the present black-screen event.

A current-board IQ40 capture after the second refused native attempt contains
8190 samples, 79 distinct byte codes and 6516 changing adjacent pairs. Its
FNV-1a checksum is `19a9d0a1`, independently recalculated from the extracted
binary. Reported context is G83, ultrafine lane 2, 5865 MHz. The board's IQ
therefore varies in this particular 204.75-us window. RF input state was not
observed, and this was not a captured black-screen onset: it cannot establish
the cause of fading black video, native gain landing or reception sensitivity.
The binary, original serial record and second refused toggle are retained
under `research/data/native_refusal_2026-10-13/`, with format, hashes and
observation limits in its README.

The shortest useful new capture pairs raw IQ with the actual loaded CVBS at
the first black event, while retaining gain/DC/filter and transport counters.
If IQ still varies and scope shows snow with malformed sync, the display/sync
path is implicated. If the same IQ becomes flat, investigate gain/DC/RX first.
If valid CVBS reaches the display but the display stays muted, acquisition
hysteresis is established independently of the demodulator.

## DC filter sharing is an assumption

`dco_context` records filter capacitor code, skirt and BW20/BW40, but
`dco_same_context` compares frequency, gain-table bounds, band and IQ scale
only. `restore_lock` replays the vendor chain, forces gain, applies the new
filter and immediately calls the DCO hook. Thus a profile/filter change can
hold a row measured under the previous filter without a new residual check.
`DCO_CONTEXT` now prints the table's filter and active filter with `filter_same`;
this is diagnostic evidence, not proof the DC codes differ or are equivalent.

Rejecting every differently filtered table is not a complete fix: the current
NVS index is keyed by channel, not channel-plus-filter, and there is no safe
automatic live replacement search. That change would remove working DC holds
after a profile switch. Keep the existing behavior until a fixed-gain,
same-channel filter A/B measures residual DC before and after the switch.
Compare both directions and high-gain rows. If materially different, store
separate startup-calibrated filter contexts and select them in the existing
PHY transaction; missing contexts must have an explicit safe fallback.

## Native gain readback: what static evidence proves

The pinned libphy setter writes RF block/bank (8,1), BB (0,2) and fine (1,2)
with fine bits `0xd0 | fine_code`. Its force-index helper instead only updates
register 0x600a702c, including the force index and enable bit. Matching a force
index alone therefore does not prove the currently held PBUS tuple.

The read-address helper and its switch relocations establish these read-only
fields:

| Tuple | Read field |
| --- | --- |
| RF (8,1) | `(REG(0x600a08a8) >> 9) & 511` |
| BB (0,2) | `REG(0x600a0894) & 511` |
| fine (1,2) | `(REG(0x600a0898) >> 9) & 511` |

The getter performs no PBUS mode change. The setter format still needs a
physical comparison against these fields in forced/DCO-held and native modes;
a hardware-native word need not satisfy the setter's format merely because
the setter uses it. A refused acquisition with zero open time is not a native
scan or a test of native sensitivity. `research/native_agc_evidence.py` now
prints 34 functions covering the earlier seven, the address helper,
force/debug/work helpers and offset/channel call paths, plus the verified
jump table, with the exact archive SHA guard.

## AFC offset versus the saved native RF context

The pinned `phy_chip_set_chan_offset` rounds the requested kHz value with
`(offset + 2) & ~3`, stores the signed 16-bit result at `phy_param + 0x1e`,
and optionally adds eight times the signed byte at `+0x92` when `+0x91` is
nonzero. It calls `phy_freq_correct(1, offset)`, disables BB AGC, calls
`phy_set_channel_rfpll_freq_new` using the existing base frequency at `+0x120`
and mode byte at `+0x31`, then enables BB AGC. A requested -180 kHz therefore
does not inherently change the base-frequency context or regenerate the gain
table through the full channel-switch function.

`phy_freq_correct` changes digital frequency-correction words at 0x600a7c30,
0x600a0c20, 0x600a0c18/1c, 0x600a7430/34, 0x600a8050 and 0x600a7898. The
RFPLL route also performs band setup, RFPLL calibration, CKGEN calibration,
frequency-memory updates and PBUS frequency setup. `phy_set_rx_pbus_freq`
writes PBUS (7,2), temporarily enters debug mode when required, and returns
through work mode; that work-mode helper can force G50 briefly before clearing
the force bit. The existing receiver restore transaction must still replay
the intended forced gain/DC after this vendor operation.

The inspected direct register writers do not overwrite saved native RF gate
0x600a705c or masked fields `0xf7080000` at 0x600a284c. BB enable/disable
touches 0x600a7030 bit 29 and pulses 0x600a702c bit 23; band configuration also
changes 0x600a7030 bit 5. Offset-sensitive PLL/I2C/frequency memory is separate
from those saved RF-AGC fields. This supports testing reuse of the boot RF-AGC
snapshot for nonzero offsets on the same base channel/BW40, but is not a full
proof of the complete transitive vendor/I2C state or physical native landing.

Simplest implementation sequence: first resolve the existing zero-offset
forced-readback refusal on the board; then compare -180/0/+180 kHz while
retaining base channel 5865 MHz, BW40, pinned library, gain-table identity,
generation, forced-tuple and rollback checks. Only after those agree should
the `offset == 0` restriction be replaced by a validated same-context test.
Do not copy post-`restore_lock` RF fields as a new native snapshot: that
transaction has already disabled the native RF controller. Broader channel
or BW support needs its own vendor-context capture before disabling it.

## Supported changes versus speculative gains

The current fixed policy already has evidence for preserving AFC estimates,
removing picture-dependent eligibility gates, LP-by-gain and expensive-observer
cadence fixes. Held-out simulations support a large wide-deviation/filter
mismatch loss and receiver-offset loss; they do not measure extra RF range.
The 1.7-deviation filter benefit did not survive held-out tests. More ADC gain,
live weak-signal AFC and replacing the tracker have no demonstrated universal
gain. Retain those negative results instead of introducing another controller.

Envelope C/N on a strong wide VTX can remain low because the analog filter
creates AM. A single global dB correction would overestimate genuinely weak
signals and may choose an unsuitable kp/AFC correction. The useful existing
fix is removing the independent phase-coherence veto while preserving settled
gain, clipping, fresh sync and validated profile evidence. Changing the C/N
tables further requires held-out deviation/filter/gain cases and real IQ.

Issues 201 and 213 remain open until the separate IQ/video/display thresholds
and affected-monitor recovery are demonstrated. Successful host fixtures,
native gate opening, pleasant snow and a green firmware build are insufficient
to close those requests.
