# TRUE80 RX oracle

This branch investigates whether C5VRX can preserve the ESP32-C5 modem's
native ~80 MS/s Q4/I4 observation stream instead of acquiring only every
second sample at 40 MS/s.

This is an **experimental hardware oracle**, not yet a claim that 80 MS/s
improves range.

## Why

Current live acquisition is:

```text
MODEM native IQ ~80 MS/s
        |
        v
PARLIO RX 40 MS/s
        |
        v
Q4/I4 ring
        |
        v
live demod
```

The MODEM dump engine has measured a native cadence of about 79.99 MS/s, while
the proven production path intentionally treats PARLIO RX as a 40 MS/s complex
IQ stream.

Dropping every second native observation before FM discrimination may discard
useful trajectory information near the weak-signal/quantization limit. Issue
#23 independently demonstrates why dropping trajectory information before FM
branch resolution is dangerous at the later 40 -> 20 MS/s stage.

## What the boot oracle does

The PR firmware runs three bounded tests after RF initialization and before the
normal live receiver starts.

### 1. PARLIO 80 MHz loopback

A deterministic pseudo-random byte stream is transmitted with PARLIO TX at
80 MHz. The TX clock is routed to GPIO2 and used as the external clock for the
PARLIO RX unit.

The RX side must show:

- approximately 80 MB/s / 80 MS/s;
- byte-exact data after a small startup alignment search;
- no timeout.

This separates the question "can PARLIO RX actually receive at 80 MHz?" from
MODEM_DIAG routing.

### 2. MODEM source-clock discovery

The ESP32-C5 MODEM SYSCON exposes:

- `FPGA_DEBUG_CLKSWITCH`;
- `FPGA_DEBUG_CLK80`;
- `FPGA_DEBUG_CLK40`.

The oracle enables CLK80 and CLK40 separately, routes each MODEM_DIAG candidate
to GPIO2, and lets external-clock PARLIO RX measure which diagnostic lane
actually behaves like the selected modem clock.

A candidate is accepted only when its measured receive cadence is within 25%
of the requested clock.

### 3. Bounded TRUE80 Q4/I4 capture

When a plausible CLK80 lane is found, the existing proven Q4/I4 lanes are
captured using that MODEM-derived clock.

Both positive and negative PARLIO sample edges are tested. The firmware reports:

- measured receive rate;
- FNV hash;
- transition count;
- near-origin rate;
- rail-hit rate;
- even/odd histogram divergence;
- Q and I nibble occupancy masks.

These metrics are characterization evidence only. The oracle deliberately does
not auto-promote one edge into production from those statistics alone.

## Safety / fallback

The oracle is bounded and runs only at boot.

After it finishes:

1. MODEM test-clock configuration is restored.
2. GPIO2 is released.
3. Q4/I4 MODEM_DIAG routing is restored.
4. The existing production `video_start()` path starts unchanged at 40 MS/s.

A failed TRUE80 test therefore does not prevent normal live video from
starting.

## Expected serial output

A successful board should show lines similar to:

```text
TRUE80 LOOPBACK rate=... MS/s offset=... mismatch=0/32768
CLK80 candidate DIAG[...] -> ... MS/s
CLK40 candidate DIAG[...] -> ... MS/s
TRUE80 clock search: CLK80=DIAG[...] ... | CLK40=DIAG[...] ...
TRUE80 POS rate=... MS/s ...
TRUE80 NEG rate=... MS/s ...
TRUE80 RESULT: PASS ...
```

If the result is incomplete, copy the full `true80_lab` boot output before
changing the implementation.

## What PASS does and does not prove

PASS would prove that:

- PARLIO RX can sustain a bounded 80 MS/s external-clock capture on the tested
  ESP32-C5;
- a MODEM-derived source-synchronous clock is observable;
- Q4/I4 can be captured at that native cadence for a bounded transfer.

PASS does **not** yet prove:

- indefinite sample-gapless operation;
- 80 MB/s cyclic GDMA stability across all boundaries;
- realtime 80 MS/s demodulation throughput;
- improved RF sensitivity or range;
- that POS or NEG is the final production edge.

## Next gate after PASS

The next architecture should use all native phase observations before reducing
the rate:

```text
Q4/I4 @ ~80M
   -> phase/confidence for every sample
   -> adjacent FM / trajectory recovery
   -> filter/combine
   -> 80 -> 40 reduction
   -> optional 40 -> 20 reduction
   -> CVBS
```

The 80 -> 40 reduction must happen **after** the useful phase/trajectory
information has been consumed. A simple "capture 80M, drop every second byte"
would reproduce the information loss this experiment is trying to measure.
