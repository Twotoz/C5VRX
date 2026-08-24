# RF ring memory and DMA path

## Addressability

The fixed dump window `0x40830000..0x4083ffff` lies inside the C5 internal,
byte-accessible DMA address range (`SOC_DMA_LOW=0x40800000`,
`SOC_DMA_HIGH=0x40860000`). Public pointer predicates therefore classify it as
internal and DMA-capable. This is **PROVEN STATICALLY** for address eligibility,
not proof that simultaneous RF-write/GDMA-read arbitration meets throughput.

C5VRX reserves the complete window before heap initialization and asserts
`_bss_end <= 0x40830000` at link time. The producer refuses availability if the
runtime DMA/internal predicates fail. This preserves fail-closed ownership.

## BitScrambler loopback

The IDF v6.0.2 loopback driver builds reusable AHB GDMA TX/RX link lists and
mounts caller-provided memory buffers for each run. Therefore:

- a contiguous fixed-ring subrange can be the input without a CPU memcpy;
- input/output and descriptor alignment must satisfy the driver's four-byte DMA
  alignment and transfer-size rules;
- descriptors and channels are reusable sequentially;
- two ring segments can be submitted alternately, but wrap must be split into
  two contiguous runs;
- the loopback attachment peripheral's DMA cannot be used concurrently by
  another owner;
- the public attachment list has I2S0/SPI/UHCI/AES/SHA/ADC/PARLIO but no direct
  Wi-Fi/FE endpoint.

The persistent WBFM context implements descriptor/LUT reuse. The queued source
ABI still requires immutable blocks, so the first guarded ring source copies
short windows into owned DMA buffers. A zero-copy ring-to-BitScrambler path must
be synchronous: check writer distance, transform the contiguous window before
overwrite, re-read the writer pointer, and discard on ambiguity. It must not
hand raw ring memory to the existing asynchronous queue.

Zero-copy is intentionally deferred until the real simultaneous benchmark
demonstrates that copying is the limiting stage. `BENCH RING PIPELINE 0 1000`
reports `copy_bytes_per_second`, `copy_cycles_total`, `copy_cpu_percent` and a
machine-readable `zero_copy_action`. The current decision threshold is measured
copy occupancy >=25% or a `COPY_AMBIGUOUS` stop; without valid copied blocks the
result is `NO_RESULT`. This prevents replacing the safer immutable contract on
the basis of an unmeasured 80 MS/s design case.

## Ordering and coherency

Ring accesses use volatile loads and RISC-V I/O/read fences around producer
ownership and completed copies. The fixed HP SRAM window is accessed as
internal memory rather than through an external cached mapping. No explicit
cache-maintenance API is currently required by a public C5 mapping rule, but
that is **LIKELY**, not a measured coherency result. A first-board walking-data
and simultaneous-writer test must verify CPU and GDMA observations agree.

The immutable-copy implementation allocates five 4096-word DMA slots (80 KiB)
only while LIVE is active. The fixed-size CVBS chunker adds one 1024-byte block;
USB preview optionally adds two 19,200-byte grayscale frames. Every allocation
is bounded and a failure leaves the RF producer stopped.

## Contention verdict

RF writer + CPU/GDMA reader + BitScrambler output + PARLIO GDMA share internal
memory/bus resources. No public arbitration guarantee proves 320 MB/s write plus
320 MB/s read. Sustainable rates, stalls and whether PARLIO deadlines survive
are **UNKNOWN**. The correct evidence is the prepared cadence/soak/ring copy and
BitScrambler/PARLIO benchmarks while the producer is active.

## Hardware-only direct probe

The first direct candidate avoids both CPU copying and the BitScrambler
loopback driver's output buffer. PARLIO owns a circular GDMA read descriptor for
the complete fixed RF block. A TX BitScrambler attached to PARLIO consumes four
packed I/Q words, emits one six-bit-biased discriminator byte, and PARLIO sends
that byte at 20 MS/s to the resistor DAC.

Normal code prepares all descriptors while the CPU still owns HP-SRAM. A
bounded routine linked into LP RAM then performs the unsafe ownership interval:

1. grant the recovered RF/MAC owner access to the dump banks;
2. enable and software-trigger the mode-0 writer;
3. measure an 8192-word writer lead;
4. enable the already-armed PARLIO clock;
5. detect each terminal pointer/status pair, lower and raise capture-enable,
   pulse the software trigger, and measure the resulting restart gap;
6. count pointer advance, completed blocks and successful restarts for 100 ms;
7. stop PARLIO, stop the writer, and restore CPU SRAM ownership.

Interrupts remain masked only across that bounded LP routine. The 100 ms test
is below the configured interrupt-watchdog window and the generated PAL logo is
restarted before USB reports the result. This proves neither bus arbitration
nor AV lock statically; `AV DIRECT PROBE` exists specifically to measure those
remaining physical properties without risking another unbounded USB/FreeRTOS
wedge.

### Physical crash finding and repair

The first physical run reached the direct handoff but then reported a C5
hardware stack-protection panic with `MEPC=0x5000023a`, an LP-RAM `SP`, and the
HP-RAM bounds of task `c5vrx_usbctl`. The cause was architectural rather than an
RF result: the assembly trampoline changed `sp`, but hardware stack bounds are
updated by ESP-IDF on a FreeRTOS context switch, not by an ordinary function
call. The repair creates a static priority-21 probe task with its own aligned
4096-byte LP-RAM stack. FreeRTOS now records those bounds before the task enters
the ownership interval; the LP kernel only allocates a normal 48-byte frame on
that already-valid stack.

The same run exposed a separate PAL teardown bug. At the XIAO profile's 100 Hz
tick rate, `pdMS_TO_TICKS(1)` is zero. A priority-20 USB task therefore never
blocked while waiting for the priority-18 PAL refill task and eventually
force-deleted it with an active looping transaction. Teardown now notifies the
PAL task, blocks for real ticks, refuses unsafe deletion on timeout, and only
then uses the documented `parlio_tx_unit_disable()` operation to terminate the
infinite DMA transaction.

Neither crash is evidence for or against usable VTX samples: both occurred
before the bounded RF observation completed. The repaired result adds an
`execute` code and `lp_stack_min_free_bytes` so the next physical run can
separate orchestration health from RF continuity and decoder lock.

### Physical one-shot finding and LP-RAM rearm

The repaired physical run completed safely and provided the decisive producer
measurement: after the 8192-word lead, the pointer advanced another 8148 words,
then parked at exactly 16383 with the completion bit asserted. The inferred
81.48 kword/s value was therefore the size of one bounded block divided by the
100 ms observation window, **not** the RF sample clock. The accessible C5 dump
writer is a 16,384-word one-shot, not a self-wrapping hardware ring.

The direct kernel now reconstructs continuity at the narrowest possible layer.
It remains in LP RAM with interrupts masked, observes the terminal state,
forces a real enable falling/rising edge, pulses the recovered mode-0 software
trigger, and waits for the pointer to leave 16383. PARLIO and its circular GDMA
consumer remain armed throughout. No scheduler, heap, USB, HP-RAM stack or CPU
copy participates in the restart. The result reports completed blocks,
successful/failed rearms and total/maximum gap cycles. Passing is deliberately
named `STITCHED_CONTINUOUS_IQ_CANDIDATE_AV_LOCK_TEST_REQUIRED`: only the board
can establish whether the measured restart gaps are short enough for a decoder
to retain horizontal and vertical lock.
