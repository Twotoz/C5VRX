# Polar11 — 40 MS/s adjacent FM in two TX bundles per 50 ns

Polar11 replaces Golden's 50 ns endpoint discriminator with a true 25 ns
adjacent discriminator while staying inside the ESP32-C5 TX BitScrambler
budget.

## Datapath

```text
MODEM_DIAG raw Q4/I4 @ 40 MS/s
  -> RX BitScrambler: raw8 -> nested Polar6
  -> 32 KiB Polar6 ring @ 40 MB/s
  -> TX BitScrambler: previous Phase5 (5) + current Polar6 (6)
  -> 11-bit / 2048-entry LUT8
  -> one 6-bit DAC result every 25 ns
  -> CVBS @ 40 MS/s unique
```

The two BitScrambler engines are independent and run in parallel.

## Nested Polar6

Polar6 is deliberately not a free-running 64-bin quantizer. It is encoded as:

```text
bits 5:1 = exact production Phase5
bit 0    = half-sector residual
```

Therefore:

```text
Polar6 >> 1 == Phase5
```

for all 256 raw Q4/I4 input bytes. The residual halves every existing Phase5
sector without changing the coarse state.

## Why the TX path fits

The C5 LUT8 mode exposes 11 address bits:

```text
previous Phase5 = 5 bits
current Polar6  = 6 bits
                  -------
                  11 bits
```

The 2048-entry LUT directly maps that asymmetric adjacent pair to calibrated
P20/G2 CVBS. In steady state one TX bundle simultaneously:

- emits the previous LUT result;
- builds the next 11-bit LUT address;
- updates previous Phase5 from current Polar6[5:1];
- reads the next Polar6 byte;
- writes one DAC byte.

So the TX cost is exactly:

```text
1 bundle / 25 ns
2 bundles / 50 ns
40 MS/s unique DAC values
```

There is no p,m,c triplet, no Phase6 pair-sum, no two-stage LUT16 backend and
no duplicated [D,D] output.

## Quantization model

The generated LUT uses circular centroids of the real Q4/I4 quantization
cells. Over the useful raw-Q4 vector set used by the repository validation,
the host oracle measures roughly:

```text
Phase5 pair:  ~4.59 deg RMS
Polar11 pair: ~3.54 deg RMS
```

This is a host-domain quantization result, not yet a live RF/CVBS claim.

## Hardware status

This PR is experimental. Polar11 changes the RX DMA-ring format, so selection
is boot-only. Use the serial `J` command to persist Polar11 and reboot; use
`J` again to return to Golden.

The first hardware gates are:

1. RX predecoder produces changing Polar6 bytes continuously at 40 MB/s.
2. TX remains free of FIFO starvation with one bundle per 25 ns.
3. CVBS sync locks at 40 MS/s unique output.
4. A/B image quality and weak-signal behavior beat Golden.
