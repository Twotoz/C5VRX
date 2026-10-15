# Postvalidated read-only detector turns

This adapts C5VRX by Twotoz and contributors from the
[canonical repository](https://github.com/Twotoz/C5VRX). The
[official website](https://twotoz.github.io/C5VRX/) carries the Discord invite.

The actual W diagnostic stopped on its first80us turn on the C5, before any
detector call. The new prototype in research/sync_read_turn.h is an isolated
alternative source-lifetime model. It is not integrated in firmware and grants
no DMA memory-write permission.

It brackets an actual read lease with completed-DONE observations, conservatively
covers the current and successor RX descriptors, records every actual raw read,
and verifies the footprint against the ending overwrite frontier. A failed turn
restores the complete private detector and invalidates progress. It does not
subtract a speculative80us maximum transfer from every source interval.
The192MB/s ceiling still excludes an unseen lap. Context, epoch, clock, lease,
future reads, expired reads and time bounds remain explicit rejection cases.

The unit test passes9216 geometry/rotation/byte-phase cases and verifies complete
rollback. The actual frozen-core replay uses the real RX descriptor lengths
(7x4092+2x2062), six seven-field PAL/NTSC clean/fade/phase-return scenes and
completed descriptor cadence at80us polling. It models an8us read duration;
that is an assumption, not a C5 measurement.

Both the existing acquisition and the temporary coarse variant retain lock and
genuine V observations with zero rejected turns or source writes in these
fixtures. The baseline has14 PAL and70 NTSC stale acquisition skips; coarse has
one, with first lock at13688us in both standards. Coarse acquisition peaks at
162 PAL/145 NTSC evaluations, versus370/437 for baseline. TRACK still reaches
728 evaluations and5880 actual raw reads in a fade. An outer192 budget therefore
still does not establish an80us service bound.

The recorder currently calls a host-only64-bit min/max hook on every raw read.
Do not move that overhead into the firmware without measuring it. Production
integration also needs actual scheduling recovery; this source-lifetime test
does not resolve the physical W overrun or establish a live camera clock.

The64-budget replay also passes all six scenes with the coarse variant:
first lock is14248us PAL/14328us NTSC. Baseline64 never acquires in these
scenes. The coarse transition can still execute297/310 evaluations when it
enters TRACK inside a call, and TRACK reaches678. Lowering the outer budget
therefore does not claim a hard time bound. These results are retained
separately in sync_read_turn_64.json. The production ACQUIRE now uses the same
63-sample discovery/3-sample backfill, and manual W requests64 evaluations;
the kernel's actual board timing still governs acceptance.

Reproduce with `python research/sync_read_turn.py`; full inputs, source hashes
and results are retained in research/data/sync_read_turn.json. The frozen raw
core is sync_read_core_cb538a1c.c.gz. New production pulse constructors are
separate: this replay runs the detector with repairs disabled throughout.
