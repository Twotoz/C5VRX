# Exact adjacent Phase5 live experiment

PR #53 adds an opt-in realtime discriminator for Issue #23. It deliberately
keeps Golden's Q4/I4 -> Phase5 quantizer and changes only the trajectory used
for the FM decision.

## Signal path

Golden:

```text
Q4/I4 @ 40 MS/s
 -> retain one Phase5 state per two raw samples
 -> 50 ns endpoint delta
 -> CVBS20
 -> [D,D] @ 40 MHz DAC
```

ADJ PHASE5:

```text
Q4/I4 @ 40 MS/s
 -> Phase5 for every raw sample
 -> d0 = wrap5(phi1 - phi0)
 -> d1 = wrap5(phi2 - phi1)
 -> LUT(d0,d1) = map(signed(d0) + signed(d1))
 -> CVBS20
 -> [D,D] @ 40 MHz DAC
```

The final LUT sees both adjacent deltas separately. It does not wrap their sum
back onto the five-bit phase circle. The experiment therefore preserves the
middle-sample winding information while keeping the same phase resolution as
Golden.

## Eight-bundle kernel

ESP32-C5 has eight BitScrambler instruction slots. `fm_adjacent.bsasm` uses
all eight:

1. one-time first raw lookup;
2. load first Phase5 state;
3. form first adjacent delta;
4. preserve d0 and look up the second raw sample;
5. load second Phase5 state;
6. form second adjacent delta;
7. address the 32x32 d0/d1 output table;
8. emit duplicated DAC bytes while prefetching the next first raw sample.

The 1024x16 LUT is dual-purpose. Low six bits always contain the final CVBS
code for the address interpreted as `d0 | d1<<5`. On raw-Q4 addresses 0..255
the upper ten bits additionally contain positive and negative Phase5 state.

## Realtime M2M schedule

The public IDF loopback driver owns both BitScrambler directions, so ADJ cannot
also use the flight PARLIO TX decorator. Its M2M output is already `[D,D]`;
PARLIO TX consumes that output ring directly.

The 32 KiB raw ring and 32 KiB adjacent output ring are split in halves:

```text
raw half 0 -> output half 1
raw half 1 -> output half 0
```

A half is transformed only after RX has completed it. Crossing the output halves
keeps the M2M writer away from the half PARLIO TX should be consuming.

At 40 MB/s, one 16 KiB raw half represents 409.6 us. Every M2M run records its
elapsed time and the scheduler rejects a run if RX has already returned to the
same raw half.

## Finite-run boundary repair

`bitscrambler_loopback_run()` resets the BitScrambler for each finite job, so
its retained previous Phase5 state is unavailable at the first output pair of a
new half. The CPU repairs exactly that one duplicated pair from:

```text
previous raw byte, input[0], input[1]
```

No whole-block copy or CPU sample loop is inserted in the live data plane.

## Safety / recovery

ADJ PHASE5 is opt-in and forces 6BIT@40. Selecting or leaving ADJ changes
BitScrambler ownership and is applied with a clean reboot.

If adjacent allocation, initialization, the first M2M run, or startup timing
fails, firmware persists GOLDEN + 6BIT@40 and reboots. A three-second BOOT
recovery from ADJ likewise restores GOLDEN + ARC before rebooting.

## Hardware A/B

Keep RF settings identical:

```text
RX PROFILE  ARC
BW          BW40
AFC         OFF
DAC         6BIT@40
```

Compare:

```text
DEMOD GOLDEN
DEMOD ADJ PHASE5
```

Use `d` on USB serial and record the Adjacent M2M line:

- `runs`
- `last/max us`
- `written`
- `short`
- `fail`
- `deadline`
- `boundary`
- transformed half counts

A useful live result requires zero short writes/failures/boundary misses and
M2M duration below the 409.6 us half-ring service window with practical margin.
Only then should visible range/static/sync differences be attributed to the
adjacent discriminator.
