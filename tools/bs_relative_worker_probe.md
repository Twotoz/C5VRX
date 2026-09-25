# Counter-A relative worker oracle

The C5 TRM defines relative mux addressing as adding Counter A to source
selectors below 64. The `+a` assembler syntax enables this for the whole
bundle. This changes source selection, not the number of LUT evaluations or
opcodes available per bundle.

`main/bs_relative_worker_probe.bsasm` is a finite two-instruction loopback
oracle. `controller` sets A to either 0 or 8 from bit zero of the first input
byte. `worker` reads the selected raw byte through `0+a..7+a`, duplicates it
to a 16-bit output word, reads the next two input bytes, and loops. The
firmware feeds 128 pairs of distinct bytes and compares all 256 output bytes
with the expected selection. A success is printed as `BS_REL_WORKER status=PASS`.
The result remains available through the existing serial `p` snapshot command
after startup, so USB reconnection cannot hide a quick boot-time result.
The finite loopback drains its prefetched window with `trailing_bytes 10`;
the live Golden programs retain their existing `trailing_bytes 0` setting.

Enable `CONFIG_C5VRX_BS_RELATIVE_WORKER_PROBE=y` for a diagnostic build. The
oracle runs once before RF/video startup and releases the loopback handle.
It is disabled in a normal build. It does not substitute its output into the
live CVBS path.

The first silicon run with `trailing_bytes 8` wrote 254 of 256 output bytes:
the two missing terminal bytes were the only mismatches. The source-selected
bytes that were written matched the expected stream. Increasing the finite
loopback drain by two bytes is intended to finish that last word.

The assembler accepted exactly two bundles. The compiled worker bundle has
`CTL_MUX_REL=1`, while the controller has `CTL_MUX_REL=0`. The default and
enabled firmware variants both build under ESP-IDF 6.0.2. A silicon result
still needs to be recorded. This probe answers the source-selection question;
it does not establish exact adjacent FM, 360-degree winding, or live PARLIO
timing on its own.
