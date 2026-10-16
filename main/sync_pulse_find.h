/* C5VRX by Twotoz/contributors: resumable exact sync-pulse observation.
 * The callback supplies original-source nine-step lowm decisions. PENDING
 * consumes no sample and never means absent sync. Geometry and source room
 * are frozen at entry; the caller owns epoch/lifetime validation per chunk.
 * This state machine grants no DMA write permission. */
#pragma once
#include "sync_pulse_walk.h"
#include <limits.h>

typedef enum {
    SPF_PULSE_NONE, SPF_PULSE_NORMAL, SPF_PULSE_EQ, SPF_PULSE_BROAD
} spf_pulse_t;

typedef enum {
    SPF_PREVIOUS, SPF_CURRENT, SPF_CONFIRM, SPF_WIDTH, SPF_REFINE2,
    SPF_REFINE1, SPF_DONE, SPF_ABORTED
} spf_status_t;

typedef struct {
    spw_t walk;
    uint64_t prediction, available;
    uint32_t width, inside;
    int window, offset, edge;
    bool previous, current;
    spf_pulse_t pulse;
    spf_status_t status;
} spf_t;

static inline spf_pulse_t spf_classify(uint32_t width, uint32_t inside)
{
    uint32_t n = width / 3u;
    if (width >= 150u && width <= 230u && inside <= n / 8u)
        return SPF_PULSE_NORMAL;
    if (width >= 900u && inside <= n / 8u) return SPF_PULSE_BROAD;
    if (width >= 70u && width <= 120u && inside <= 1u) return SPF_PULSE_EQ;
    return SPF_PULSE_NONE;
}

static inline bool spf_init(spf_t *s, uint64_t prediction, int window,
                            uint64_t available)
{
    if (!s) return false;
    *s = (spf_t){.prediction = prediction, .available = available,
        .window = window, .offset = -1, .status = SPF_ABORTED};
    if (window < 0 || window > INT_MAX - 3 ||
        prediction < (uint64_t)window + 8u ||
        prediction > UINT64_MAX - (uint64_t)window - 1310u) return false;
    s->offset = -window;
    s->status = prediction + (uint64_t)window + 8u > available ?
        SPF_DONE : SPF_PREVIOUS;
    return true;
}

static inline void spf_next(spf_t *s)
{
    s->previous = s->current;
    s->offset += 3;
    s->status = s->offset > s->window ? SPF_DONE : SPF_CURRENT;
}

/* Run until completion, source expiry, or callback backpressure. No caller
 * output is published while pending. Every resumed callback request is the
 * exact request at which the preceding call yielded. */
static inline spf_status_t spf_run(spf_t *s, spw_sample_fn sample, void *ctx)
{
    if (!s) return SPF_ABORTED;
    if (s->status == SPF_DONE || s->status == SPF_ABORTED) return s->status;
    if (!sample) { s->status = SPF_ABORTED; return s->status; }
    while (s->status != SPF_DONE && s->status != SPF_ABORTED) {
        uint64_t k = s->prediction + (uint64_t)(int64_t)s->offset;
        if (s->status == SPF_WIDTH) {
            uint32_t used;
            spw_status_t status = spw_run(&s->walk, sample, ctx, UINT32_MAX, &used);
            if (status == SPW_ACTIVE) return s->status;
            if (status != SPW_DONE || !spw_result(&s->walk, &s->width, &s->inside)) {
                s->status = SPF_ABORTED;
                return s->status;
            }
            s->pulse = spf_classify(s->width, s->inside);
            if (s->pulse == SPF_PULSE_NONE) spf_next(s);
            else s->status = SPF_REFINE2;
            continue;
        }
        uint64_t at = k;
        if (s->status == SPF_PREVIOUS)
            at = s->prediction - (uint64_t)s->window - 3u;
        else if (s->status == SPF_CONFIRM) at += 3u;
        else if (s->status == SPF_REFINE2) at -= 2u;
        else if (s->status == SPF_REFINE1) at -= 1u;
        bool low;
        spw_sample_result_t result = sample(ctx, at, &low);
        if (result == SPW_PENDING) return s->status;
        if (result != SPW_SAMPLE) { s->status = SPF_ABORTED; return s->status; }
        switch (s->status) {
        case SPF_PREVIOUS:
            s->previous = low; s->status = SPF_CURRENT;
            break;
        case SPF_CURRENT:
            s->current = low;
            if (low && !s->previous) s->status = SPF_CONFIRM;
            else spf_next(s);
            break;
        case SPF_CONFIRM:
            if (low) {
                uint64_t room = s->available > k + 12u ? s->available - k - 12u : 0u;
                if (!spw_init(&s->walk, k, room < 1300u ? (uint32_t)room : 1300u)) {
                    s->status = SPF_ABORTED; return s->status;
                }
                s->status = SPF_WIDTH;
            } else spf_next(s);
            break;
        case SPF_REFINE2:
            if (low) { s->edge = s->offset - 2; s->status = SPF_DONE; }
            else s->status = SPF_REFINE1;
            break;
        case SPF_REFINE1:
            s->edge = s->offset - (low ? 1 : 0); s->status = SPF_DONE;
            break;
        default:
            s->status = SPF_ABORTED;
            break;
        }
    }
    return s->status;
}

static inline bool spf_result(const spf_t *s, spf_pulse_t *pulse,
                              int *edge, uint32_t *width)
{
    if (!s || !pulse || !edge || !width || s->status != SPF_DONE) return false;
    *pulse = s->pulse;
    if (s->pulse != SPF_PULSE_NONE) { *edge = s->edge; *width = s->width; }
    return true;
}
