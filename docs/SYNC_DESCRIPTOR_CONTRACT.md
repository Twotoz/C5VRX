# Whole-descriptor memory admission

C5VRX by Twotoz and the C5VRX contributors. This is a concrete candidate
adapter for the actual GDMA ring; no shared writer is enabled.

## Primary operating contract

The inspected [Espressif ESP32-C5 TRM](https://documentation.espressif.com/esp32-c5_technical_reference_manual_en.pdf)
identifies itself as Version1.1. Local PDF SHA256:
`b2e7944dec0ee8e933042de69eb418da28a959d75afddde660775e9395739240`.

- Sections5.3–5.4.5, pp185–189, define sequential linked-list transfers,
  descriptor payload buffers and descriptor validation before data transfer.
- Registers5.31/5.38, pp224/227, identify the next pre-read descriptor as
  not yet processed. Registers5.34–5.36 and5.41–5.43 identify current BF0,
  successor and previous pre-read descriptor addresses.
- Section5.6, pp193–194, and registers5.51/5.52, p235, describe DONE as a
  completed channel transfer. DONE is separate from the disabled EOF path.

These are usable operating semantics, not a demand for zero-latency status
publication. The conservative engineering model treats the entire current
descriptor **and its entire successor** as potentially transferred already.
It then bounds subsequent memory motion from the first register-read time.
This removes the unsupported assumption that one64-byte burst bounds all
unpublished payload. It does not infer a whole FIFO capacity from a count's
bit width. TRM5.4.7 lists SINGLE/INCR4/INCR8, while the pinned LL also has an
INCR16 selection; neither burst length limits aggregate queued transfers.

The remaining physical assumptions are normal coherent MMIO/current-next
sequencing, DONE completion semantics, verified memory/cache ordering and a
validated whole-copy publication WCET. They need targeted validation on this
C5 configuration; they are not new manually-set proof booleans.

## Exact pre-store rule

`main/sync_descriptor_window.h` validates immutable RX/TX descriptor geometry
once per stream generation/relative epoch. For each proposed commit it
requires matching bracketed actual BF0/DONE/OUT_DSCR/OUT_STATE/PCR tuples,
correct predecessor/successor links, unchanged raw context, known clocks,
correct absolute descriptor-start phase, fresh source and no hidden full lap.
Absolute coordinates must come from the actual monotonic progress observer;
the helper does not manufacture laps from modulo addresses.

Let `R,T` be RX/TX current descriptor starts, and let the two-node covers be
their current-plus-successor byte lengths. For a proposed commit ending
after`dt`microseconds from the **first** register read:

```
motion = 192 * (dt + 1)       # bytes; extra1us covers timer quantization
TXupper = T + TXcover + motion
RXupper = R + RXcover + motion
safe_first = ceil64(max(TXupper, RXupper-ring, source_fresh_from))
safe_last  = floor64(R - protected_history)
```

The entire cache-rounded pulse, at most1280 raw bytes, must fit inside that
one interval. No partial pulse approval is returned. The last pre-store
check includes capture time, all elapsed preparation since capture and the
entire proposed copy/cache/barrier budget. Expensive synthesis happens before
that final check. A measured overrun afterward cannot repair a torn write;
the actual IRQ/cache-safe path must respect its declared WCET.

The192MB/s bound is the documented48MHz AHB ceiling times one32-bit beat,
conservative against accepted actual AHB<=40MHz. It bounds memory traffic,
not just the40MS/s source rate. This is why a long scheduler preemption must
invalidate a prospective write even if PARLIO itself kept streaming slowly.

## Why24KiB startup lag helps

For long nodes, maximum original CPU history8216 plus64-byte margin gives
8280. Whole-current-plus-NEXT TX cover is8184. Approximate room before
copy motion/cache alignment is therefore`lag-16464`bytes:

| RX/TX start lag | Approximate initial room | Whole1280 pulse |
|---|---:|---|
|16KiB| -80bytes | Often impossible; no threshold should invent room |
|24KiB|8112bytes| Up to about35.6us before alignment/capture margins |

Actual node floors/tails vary the window, so the adapter always checks actual
frontiers. A24KiB startup target adds204.8us over the existing16KiB delay;
it does not allocate more RAM or put CPU pacing in the sample stream.
Intended startup delay is not evidence of actual lag, especially through
driver readiness waits. Physical observed separation remains authoritative.

`test/test_sync_descriptor_window.c` passes2,654,208 exact byte phases across
all81 RX/TX discovery rotations, actual7×4092+2×2062 RX and8×4092+32 TX.
Every modeled24KiB-lag case admits1280bytes with a5us copy budget, including
the7us total capture/quantization envelope. The16KiB model refuses2,318,868
cases. Tests include all64 pulse alignments, wrong registers/clocks,
generation/epoch/context, startup freshness, expired capture, full-lap
ambiguity, oversized pulse and near-64-bit overflow. These are contract
tests, not measured C5 timing or proof of LCD lock.

## Read-only W can use retrospective lifetime checks

A reader may checkpoint its private detector/clock state, read a source
interval ending no later than the starting DONE-complete boundary`D0`, then
capture ending DONE`D1`and its matching whole-current successor. Under the
same transfer operating contract, conservatively accept only if the source
starts at or after`end(current_after_D1)-ring`, stays within the fresh epoch,
and all context/capture checks hold. Including another complete successor
is available as the same conservative publication reserve.

The192MB/s/time bound still excludes a hidden full lap between observations;
it need not subtract80us×192bytes prospectively from every source interval.
For example80us plus one4092-byte descriptor is19452bytes, below one ring.
If ending bounds fail, restore the private detector/clock checkpoint and
publish no source evidence. Finish CPU source loads and ordering before the
ending capture. This is valid for read-only rollback; it cannot undo an
unsafe shared-ring store.

## Physical acceptance sequence

1. Verify actual RX/TX separation after the revised halted startup, recording
   DONE/BF0/STATE/next and clocks over many wraps, tails and restarts. Reject
   readiness/ownership/context gaps rather than infer the intended24KiB lag.
2. Measure the exact IRAM copy/cache/barrier envelope for all pulse alignments,
   including split ring-end copies and1280-byte pulses, under actual DMA and
   USB/observer load. Synthesis/preparation is outside that envelope.
3. In a private test chain, use distinct per-descriptor payload patterns to
   compare current/next/DONE readback with actual PARLIO output around the
   fetch transition. The expected next-not-processed meaning and completion
   relationship must survive the chosen clock/cache configuration.
4. Begin with read-only checkpointed W admission. Then admit one complete
   shared pulse under exclusive raw ownership and the actual bound, checking
   output and unchanged surrounding IQ/DAC. Preserve the existing original
   evidence history and keeper self-provenance protections.

No policy flag or nominal1280-byte memcpy speed substitutes for those actual
frontier, ordering, context, whole-copy and monitor observations.
