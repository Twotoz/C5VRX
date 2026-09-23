# Phase5+ two-bundle demodulator

## Hardware disposition: blocked by C5 half-duplex BitScrambler

The implementation below is a valid host-side DSP/LUT experiment but **cannot
run as a live ESP32-C5 receiver**. The [ESP32-C5 datasheet, section 4.2.1.12](https://documentation.espressif.com/esp32-c5_datasheet_en.html)
states that its BitScrambler RX and TX channels support only half-duplex
operation and cannot work simultaneously. The [technical reference manual,
BitScrambler chapter](https://documentation.espressif.com/esp32-c5_technical_reference_manual_en.pdf)
states the same restriction. Phase5+ requires RX to predecode IQ while TX
demodulates the same live stream, so its hardware topology violates this limit.

Live A/B on the ESP32-C5 v1.0 board, A1/5865 MHz, BW40, manual gain G62:

| Firmware | RX DMA window | Observed output |
| --- | --- | --- |
| Golden TX, RX BitScrambler disabled | 32 low-five-bit values, mixed raw IQ bytes | Video with VTX on |
| Golden TX, RX BitScrambler identity passthrough enabled | 4,092/4,092 bytes `0xFF` | Black screen |
| Same identity passthrough with hardware prefetch and RX started before BitScrambler | 4,092/4,092 bytes `0xFF`; RX BS `ctrl=0x000000c1`, `state=0x00030002` (`in_run=1`) | VTX off; video not evaluated |

The RX GDMA pointer still advanced and transport fault counters were zero.
The identity program removes all phase math and LUT quantization from the
test, so the static is upstream of Phase5+ arithmetic. Enabling prefetch and
changing start order did not restore data. The observed `0xFF` pattern by
itself does not identify which internal mux or FIFO supplies those bytes; the
documented half-duplex limit is the decisive reason to retire this topology.

Do not revive an RX-BitScrambler plus TX-BitScrambler variant, including a
one-bit winding preprocessor. A successor must retain raw IQ capture and use
only the TX BitScrambler in the realtime path, or use a separately proven
accelerator that can run concurrently with TX. A host oracle and two TX
bundles alone are insufficient evidence of a runnable C5 design.

## Host-side experiment

Phase5+ fixes a specific error in the 25 ns Polar11 experiment: clipping two
adjacent FM differences separately makes a temporary middle-sample phase error
survive as video. A 0° → 50° → 0° excursion produces 63 and 0 in Polar11,
whose average is 31.5 instead of the black pedestal 20.

The proposed RX BitScrambler converts each 40 MS/s raw Q4/I4 byte to the
production five-bit phase plus a three-bit envelope class. The TX BitScrambler reads three
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
The TX steady state is exactly two BitScrambler bundles per 50 ns. The proposed
RX program runs at one bundle per 25 ns and keeps every sample, though it
compresses raw Q4/I4 amplitude and does not preserve all eight original IQ bits.

The host tests exhaust all 32,768 phase triplets and execute the actual TX
instruction bundles against 2,046 random pairs. They do not prove a live
pipeline; the hardware A/B above disproves this RX+TX topology. ARC remains
manual in this mode because the existing gain controller expects raw Q4/I4
bytes, not phase/envelope codes.
The serial `P` command reports the Phase5+ ring's number of distinct phase
states, envelope-class histogram, and first four bytes without changing gain
or the video datapath. `J` selects Phase5+ or Golden and reboots because the
ring formats differ.
