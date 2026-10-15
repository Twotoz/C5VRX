# Protect original CPU evidence by keeping newest history untouched

C5VRX by Twotoz and the C5VRX contributors. This extends the centralized
`transport.c` reader tokens, DMA progress epochs and keeper commit guards.
`main/raw_history.h` supplies the enforced CPU-copy request limits and a
**candidate past-write history helper without a live writer caller**.
It does not enable shared-ring writes or prove physical DMA publication.

All actual CPU raw reads are centralized in five transport APIs. Gain,
sentinel, calibration, control/video watchdog and USB snapshot processing
continue on private copies after their reader token ends. Diagnostics read
derived measurements. `copy_recent_endpoints` is used by the scanner with
4092 pairs, consuming an8184-byte raw envelope, rather than merely4092bytes.

## One preserved-history rule

Let `R` be the absolute logical **active RX descriptor start** in one valid
relative progress epoch. It must be backed by proved contiguous RX memory
publication. It is not an arbitrary finer write pointer inside the active
descriptor: using a pointer4091bytes farther ahead could expose CPU history.

For the physical RX layout7×4092+2×2062, the newest full4092-byte node can
start8216bytes behind`R` when both short nodes intervene. Snapshot and endpoint
read envelopes need no more8192bytes. Preserve
`H=max(8216,8192)+64=8280bytes` behind`R`.

The helper's allowable past interval is:

```
first = ceil64(max(fresh_from, R - ring_bytes))
last  = floor64(R - H)
```

The whole cache-rounded commit must fit inside`[first,last)`. Ceil/floor
avoid corrupting source bytes at either boundary when cache maintenance
rounds outward; an already aligned boundary loses no extra64bytes. Ring
size32768 is a multiple of64, and the progress coordinate must retain physical
ring phase. The helper checks`R` against the validated descriptor starts.

The enforced public CPU-reader bounds:

| API | Maximum/fixed request |
|---|---|
| control | 1..4092bytes |
| probe | fixed four64-byte regions, final region at4028..4092 |
| sentinel | fixed64bytes at1984..2048 of its selected completed node |
| IQ snapshot | fixed8190bytes ending at active descriptor start |
| recent endpoints | 1..4092pairs; only odd bytes of an8184-byte maximum envelope |

Current callers fit these limits. All five public wrappers now reject null
destinations and undeclared requests before issuing a reader token or touching
raw data. The endpoints API is constrained to its actual scanner requirement.
Actual-source wrapper tests check invalid/overflow-sized requests, maximum
valid sizes, no inner-copy calls on rejection and no leaked reader tokens.

## Why a ledger test on every private copy is avoidable

A repair admitted before`R-H` is older than every reader's current source
envelope. As RX advances monotonically, the newest completed descriptor
start advances and every supported reader's earliest logical byte also
advances. That repaired **logical sample** therefore cannot enter a later
reader envelope. When its physical address is encountered in a new lap,
contiguous RX must already have overwritten it with the new logical sample.
Only that real overwrite removes repair taint in the test oracle.

Already-started readers are different: the existing raw tokens must exclude
a writer until they finish, even if their saved generation/context becomes
stale. Do not clear active tokens when a new epoch begins. Context/generation
changes make the private result unusable; they do not stop its CPU copy.

This avoids repair-ledger lookups on these tightly bounded evidence readers.
The keeper's own old-source detector still needs its original/modified
provenance rules; its older reads are outside this newest-history contract.
It cannot qualify its synthetic sync as a camera merely because CPU gain/
AFC snapshots are protected.

## Adversarial test and remaining gates

`test/test_raw_history.c` patches **every byte** in the allowed past interval,
then advances a byte-exact RX oracle through two full laps. It queries actual
reader envelopes at every byte position from all nine initial descriptor
starts:589824positions and2949120queries. It checks discovery rotations,
all64 cache-alignment offsets, arbitrary freshness origins, request bounds,
relative startup, stale generation/epoch, unknown publication, overflow and
descriptor-start admission. Pinned IDF6.0.2 host compilation passes.

That is a finite adversarial model of the stated contract. Physical coherent
frontiers and cache behavior, bounded writer WCET, TX consumption/prefetch,
RX overwrite margin and complete-pulse commit
remain independent gates. No shared writer is enabled by this helper.
