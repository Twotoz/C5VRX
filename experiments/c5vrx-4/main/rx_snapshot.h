/* C5VRX by Twotoz and contributors. Control-only DMA copy acceptance. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

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
