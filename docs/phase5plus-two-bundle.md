# Phase5+ two-bundle demodulator

Phase5+ fixes a specific error in the 25 ns Polar11 experiment: clipping two
adjacent FM differences separately makes a temporary middle-sample phase error
survive as video. A 0° → 50° → 0° excursion produces 63 and 0 in Polar11,
whose average is 31.5 instead of the black pedestal 20.

The RX BitScrambler converts each 40 MS/s raw Q4/I4 byte to the production
five-bit phase plus a three-bit envelope class. The TX BitScrambler reads three
successive phases `p,m,c`, decides whether the middle sample proves that the
endpoint difference crossed ±180°, then maps the *single* 50 ns result to the
DAC. Its 20 MS/s output is held as `[D,D]` at the physical 40 MHz DAC clock.

```
e = wrap32(c - p)
d0 = wrap32(m - p)
d1 = wrap32(c - m)

if abs(d0) <= 12 and abs(d1) <= 12 and
   abs(d0-d1) <= 6 and abs(d0+d1) >= 16:
    delta = d0+d1
else:
    delta = e

DAC = P20/G2(delta), clipped once
```

For incoherent endpoints near half a turn, the LUT approximates Golden's
pedestal suppression rather than sending them to a DAC rail. The 32³-entry
oracle factors into a 1024×16 LUT queried twice per 50 ns pair. Its first
lookup produces one of 30 five-bit tokens; its second produces the DAC value.
The TX steady state is exactly two BitScrambler bundles per 50 ns. RX runs in
parallel at one bundle per 25 ns and keeps every sample, though it compresses
raw Q4/I4 amplitude and does not preserve all eight original IQ bits.

The host tests exhaust all 32,768 phase triplets and execute the actual TX
instruction bundles against 2,046 random pairs. They do not prove that the RX
BitScrambler feeds the DMA ring correctly on a live ESP32-C5. Phase5+ remains
experimental until ring contents, transport counters, CVBS lock, and image
quality are checked on hardware. ARC remains manual in this mode because the
existing gain controller expects raw Q4/I4 bytes, not phase/envelope codes.
The serial `P` command reports the Phase5+ ring's number of distinct phase
states, envelope-class histogram, and first four bytes without changing gain
or the video datapath. `J` selects Phase5+ or Golden and reboots because the
ring formats differ.
