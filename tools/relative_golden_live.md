# Relative Golden live oracle

This branch keeps Golden's calibrated 50 ns DAC mapping while moving Counter A
to the source-selection role. It is an opt-in 6-bit TX BitScrambler program;
the normal firmware still loads the proven capture program.

The LUT16 word is repacked: bits 0..4 contain `Phase5(address & 255)`, while
bits 8..13 contain the original Golden DAC code at the full ten-bit address.
In each controller bundle, `L0..4` supplies the endpoint Phase5 and the
previous phase remains in `O26..30`; the resulting pair address is exactly
Golden's. The controller sets A=8. In the worker bundle, relative `L0+a..L5+a`
selects LUT bits 8..13 for the DAC, while relative `0+a..7+a` selects the
endpoint byte at FIFO bits 8..15. The worker subtracts eight from A. Four
copies fill the eight instruction slots, using the previously tested slot-7
fallthrough and still executing only two bundles per 50 ns pair.

`python tools/gen_relative_golden.py` checks 256 random 512-byte streams
against the original Golden BitScrambler model after two startup pairs, and
checks that both the phase table and every one of the 1024 DAC entries are
byte-identical to Golden. The program is generated and built with
`CONFIG_C5VRX_RELATIVE_GOLDEN_LIVE=y` for a live test.

This first version always chooses the endpoint byte. It tests live relative
mux selection with the real LUT, DMA and 40 MHz PARLIO path. It does not
select the middle byte, retain all middle IQ information, perform adjacent
FM, or correct winding. Those require separate information and arithmetic
capacity proofs after the live equivalence check.
