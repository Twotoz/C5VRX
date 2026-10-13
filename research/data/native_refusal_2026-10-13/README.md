# Native refusal / IQ record, 2026-10-13

C5VRX by Twotoz and contributors. Actual COM10 board observations, not a
native-AGC sensitivity test. RF input and monitor state were not observed.

`second-toggle.log` records the second attempt after returning to A1 at zero
offset: native mode disabled with result 1, zero open time and no landing.
Direct Gain remained active. The first attempt had offset -180 kHz and was
also refused. Installed firmware reports `5.0.0-pr.212`; the precise installed
commit was not independently read from the board.

`iq40.bin` contains 8190 consecutive raw IQ bytes, I-high/Q-low signed 4-bit,
40 MS/s: a 204.75-us snapshot after the refused attempts. Its console header
reports G83, ultrafine lane 2, 5865 MHz. `iq40-serial.log` preserves the source
record. Console status and the capture header share a line; the complete
`IQSNAP_` records were extracted and the normal offset/length/FNV verifier
was applied without modifying IQ payloads.

- FNV-1a: `19a9d0a1`.
- SHA256: `1282175ff8c71cadc2c443685713143ebe379891be8b0fb78cca3c6934d4a972`.
- Distinct byte codes: 79.
- Adjacent pairs whose bytes differ: 6516 of 8189.

This proves varying received IQ in this window. It does not identify the
black-screen onset, establish valid CVBS, or demonstrate a native scan.
