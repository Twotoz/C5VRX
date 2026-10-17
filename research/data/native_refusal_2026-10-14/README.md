# Native preflight: channel-mode bit in PBUS BB

C5VRX by Twotoz and the C5VRX contributors. Physical captures from the
checksum-verified PR 212 application `b0b4147`, 2026-10-14.

At A1 / 5865 MHz, zero offset, gain 83, native preflight reports ownership
but rejects BB `0x17f` against table BB `0x7f`. RF `0x1e7` and fine `0x0d0`
match. No acquisition opened (`open_us=0`); native mode returned to Direct
Gain. The pinned vendor instruction fixture `research/native_pbus_contract.py`
independently proves the RX-list channel-mode flag enters BB readback bit
eight. The fix checks the expected `0x100` prefix in this existing bench
context before stripping it, and continues to reject bit seven or an
unexpected mode. It does not establish wider channel/BW support.

The second capture confirms restoration to the operator's A2, OUTPUT 2.0x,
live source, native disabled. RF input and actual monitor behavior were not
controlled or measured. These captures do not prove a completed native scan.
