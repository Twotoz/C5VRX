/* C5VRX by Twotoz and contributors. Control-only DMA copy acceptance. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Newest completed descriptor holding at least `bytes`, searching backward
 * from the one just before `active`. The 32-KiB ring ends in a short 32-byte
 * tail node; skipping it keeps control copies available instead of refusing
 * one window in nine. Returns -1 if no completed node is large enough. */
static inline int rx_snapshot_pick(const uint32_t *length, int count, int active,
                                   size_t bytes)
{
    if (!length || count < 3 || active < 0 || active >= count) return -1;
    for (int back = 1; back < count - 1; ++back) {
        int idx = (active - back + count) % count;
        if (length[idx] >= bytes) return idx;
    }
    return -1;
}

/* Bytes DMA must write after finishing `active` before it reaches `sample`:
 * the nodes strictly between them in ring order. A node after `sample`
 * (such as a skipped short tail) gives no protection. */
static inline uint64_t rx_snapshot_gap_bytes(const uint32_t *length, int count,
                                             int active, int sample)
{
    uint64_t bytes = 0;
    if (!length || count < 1 || active < 0 || active >= count ||
        sample < 0 || sample >= count) return 0;
    for (int k = (active + 1) % count; k != sample; k = (k + 1) % count)
        bytes += length[k];
    return bytes;
}

/* A descriptor pointer can return to the same value after an entire lap.
 * Use both forward progress and a deadline based on actual intervening bytes.
 * Assume zero time remains in the initially active descriptor. */
static inline bool rx_snapshot_safe(int active, int sample, int after, int count,
    uint64_t safe_bytes, uint64_t rate_hz, uint64_t start_us, uint64_t end_us)
{
    if (count < 3 || active < 0 || active >= count || sample < 0 ||
        sample >= count || after < 0 || after >= count || active == sample ||
        !rate_hz || end_us < start_us) return false;
    int advance = (after - active + count) % count;
    int distance = (sample - active + count) % count;
    return advance < distance &&
           end_us - start_us < safe_bytes * 1000000ULL / rate_hz;
}
