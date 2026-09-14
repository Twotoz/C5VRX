# Issue 20: 4-bit PARLIO @ 80 MHz Packing & Grouped-DAC Transport Proof

## Overview

Issue #20 investigates using **4-bit PARLIO TX packing at 80 MHz** to provide an 80 MS/s physical DAC cadence (12.5 ns sample hold) while keeping upstream DMA / BitScrambler demand bounded at the proven **40 MB/s** rate.

## Architectural Hypothesis

With an 8-bit PARLIO bus, 80 MS/s requires:
```text
80 MS/s * 8 bit = 80 MB/s FIFO throughput
```
Physical tests on ESP32-C5 (TX80, Phase5 TX80, Linear80) proved that 80 MB/s exceeds the sustained throughput of the internal AHB/DMA bus, triggering sticky `tx_fifo_rempty` (FIFO starvation).

With a 4-bit packed PARLIO bus (`data_width = 4`):
```text
80 MS/s * 4 bit = 320 Mbit/s = 40 MB/s upstream DMA throughput
```
Because 40 MB/s is already proven 100% reliable and continuous in Golden Phase5, this potentially allows physical 12.5 ns sample updates without starving the TX FIFO.

## 4-Bit Grouped DAC Mapping

The existing 6-resistor DAC has nominal branch weights:
```text
1, 2, 4, 8, 16, 32
```
Using the ESP32-C5 GPIO matrix, PARLIO output signals 48..51 are routed to the 6 DAC pads:
- **Bit 0 (Signal 48)**: GPIO 11 (weight 4)
- **Bit 1 (Signal 49)**: GPIO 12 (weight 8)
- **Bit 2 (Signal 50)**: GPIO 8 (weight 16) + GPIO 23 (weight 1) = **weight 17**
- **Bit 3 (Signal 51)**: GPIO 9 (weight 32) + GPIO 24 (weight 2) = **weight 34**

### Monotonicity Proof

The 16 effective codes on the 0..63 DAC scale:
| 4-bit Code | Active Weights | Effective Level | Step Size |
|:---:|:---|:---:|:---:|
| 0 | 0 | 0 | - |
| 1 | 4 | 4 | +4 |
| 2 | 8 | 8 | +4 |
| 3 | 4 + 8 | 12 | +4 |
| 4 | 17 | 17 | +5 |
| 5 | 17 + 4 | 21 | +4 |
| 6 | 17 + 8 | 25 | +4 |
| 7 | 17 + 12 | 29 | +4 |
| 8 | 34 | 34 | +5 |
| 9 | 34 + 4 | 38 | +4 |
| 10 | 34 + 8 | 42 | +4 |
| 11 | 34 + 12 | 46 | +4 |
| 12 | 34 + 17 | 51 | +5 |
| 13 | 34 + 21 | 55 | +4 |
| 14 | 34 + 25 | 59 | +4 |
| 15 | 34 + 29 | 63 | +4 |

- **Strictly monotonic**: Every single step is either 4 or 5 LSBs.
- **Full scale**: Exactly 63 / 63 (identical to the 6-bit DAC full scale).
- **Linearity**: Maximum deviation from ideal 4.2 LSB step is < 0.8 LSB.

## Stage 1 & 2 Hardware Oracle

Implemented in [parlio4_80m_oracle.c](file:///C:/Users/leonb/Twotoz/C5VRX-issue11-output/main/parlio4_80m_oracle.c) and enabled via `CONFIG_C5VRX2_MODE_PARLIO4_80M_ORACLE`:

1. **Test 0 (Baseline Control)**: Direct TX40 (8-bit @ 40 MHz) timed one-shot transfer.
2. **Test 1 (Timed One-Shot)**: 80,000 bytes @ 80 MHz, measures actual DMA MB/s and physical sample cadence (target 40 MB/s, 80 MS/s, 12.5 ns).
3. **Test 2 (Sustained Continuous Loop)**: Runs for 500 ms (>1200 DMA wraps, ~20 MB transmitted) while checking sticky `tx_fifo_rempty`.
4. **Test 3 (RX Loopback & Nibble Match)**: Captures 4000 samples at 80 MS/s using PARLIO RX, tests LSB vs MSB packing hypotheses, and verifies 0-mismatch cyclic ramp match.
5. **Test 4 (GPIO Matrix Fanout)**: Captures DAC pins using PARLIO RX to physically verify that GPIO 23 mirrors GPIO 8 (Signal 50) and GPIO 24 mirrors GPIO 9 (Signal 51).
6. **Test 5 (DAC Monotonicity)**: Evaluates the 16 DAC levels and verifies monotonicity.
7. **Persistence**: Saves binary diagnostic record into the `diagcap` partition.
