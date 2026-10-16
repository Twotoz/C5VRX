/* C5VRX by Twotoz and contributors: exact resumable stride-three pulse width.
 * This is algorithm state only; the caller owns source lifetime validation. */
#ifndef C5VRX_SYNC_PULSE_WALK_H
#define C5VRX_SYNC_PULSE_WALK_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    SPW_SAMPLE,                 /* *low contains one available observation */
    SPW_PENDING,                /* observation unavailable; no state advance */
    SPW_EXPIRED                 /* source invalid; abandon this walk */
} spw_sample_result_t;

typedef spw_sample_result_t (*spw_sample_fn)(void *context, uint64_t sample,
                                             bool *low);

typedef enum { SPW_ACTIVE, SPW_DONE, SPW_ABORTED } spw_status_t;

typedef struct {
    uint64_t start;
    uint32_t max, w, total, highs;
    spw_status_t status;
} spw_t;

static inline void spw_abort(spw_t *s)
{
    if (!s) return;
    s->w = s->total = s->highs = 0u;
    s->status = SPW_ABORTED;
}

/* Start and max are frozen until the next explicit initialization. The final
 * increment may overshoot max by two, as in the original scalar recurrence.
 * Refuse overflow before accepting any sample, including at the absolute end. */
static inline bool spw_init(spw_t *s, uint64_t start, uint32_t max)
{
    if (!s) return false;
    s->start = start;
    s->max = max;
    spw_abort(s);
    if (max > UINT32_MAX - 2u) return false;
    if (max) {
        uint32_t last = ((max - 1u) / 3u) * 3u;
        if (start > UINT64_MAX - last) return false;
    }
    s->status = max ? SPW_ACTIVE : SPW_DONE;
    return true;
}

/* budget counts successful callback observations, never merely retry attempts.
 * PENDING and exhausted budgets preserve every recurrence variable. EXPIRED
 * discards partial evidence. A terminal state makes no further callback. */
static inline spw_status_t spw_run(spw_t *s, spw_sample_fn sample, void *context,
                                    uint32_t budget, uint32_t *used)
{
    if (used) *used = 0u;
    if (!s) return SPW_ABORTED;
    if (s->status != SPW_ACTIVE) return s->status;
    if (!sample) { spw_abort(s); return s->status; }
    uint32_t count = 0u;
    while (count < budget) {
        bool low = false;
        spw_sample_result_t result = sample(context, s->start + s->w, &low);
        if (result == SPW_PENDING) break;
        if (result != SPW_SAMPLE) { spw_abort(s); break; }
        if (low) s->highs = 0u;
        else { ++s->highs; ++s->total; }
        s->w += 3u;
        ++count;
        if (s->w >= s->max || s->highs >= 2u) {
            s->status = SPW_DONE;
            break;
        }
    }
    if (used) *used = count;
    return s->status;
}

/* Refusal leaves caller outputs untouched; partial widths are never results. */
static inline bool spw_result(const spw_t *s, uint32_t *width, uint32_t *inside)
{
    if (!s || s->status != SPW_DONE || !width || !inside) return false;
    *width = s->w - 3u * s->highs;
    *inside = s->total - s->highs;
    return true;
}

#endif
