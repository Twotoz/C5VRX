/* C5VRX by Twotoz/contributors: completed descriptor selection/proof.
 * A 32768-byte ring contains 8 x 4092-byte nodes plus a 32-byte tail. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef struct {
    unsigned index, distance;
    size_t safe_bytes;
} control_copy_plan_t;

static inline bool control_copy_plan(const size_t *lengths, unsigned count, unsigned active,
                                      size_t need, control_copy_plan_t *out)
{
    if (!lengths || !out || count < 3 || active >= count || !need) return false;
    unsigned idx = active;
    for (unsigned back = 1; back < count; ++back) {
        idx = (active + count - back) % count;
        if (lengths[idx] < need) continue;
        const unsigned distance = (idx + count - active) % count;
        size_t safe = 0;
        for (unsigned step = 1; step < distance; ++step)
            safe += lengths[(active + step) % count];
        if (!safe) return false;  /* no guaranteed time left before this source */
        *out = (control_copy_plan_t){idx, distance, safe};
        return true;
    }
    return false;
}

static inline bool control_copy_current(const control_copy_plan_t *plan, unsigned count,
                                         unsigned active, unsigned after, uint64_t us, uint32_t rate_hz)
{
    if (!plan || !rate_hz || count < 3 || active >= count || after >= count) return false;
    unsigned advance = (after + count - active) % count;
    return advance < plan->distance && us < (uint64_t)plan->safe_bytes * 1000000u / rate_hz;
}
