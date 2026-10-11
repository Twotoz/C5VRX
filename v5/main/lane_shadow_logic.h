/* C5VRX by Twotoz/contributors: bounded, read-only IQLaneFusion proposals. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "lane_shadow_table.h"

typedef struct {
    uint32_t epoch, gain_epoch, windows;
    uint64_t since_us, last_us;
    uint8_t pending, proposed, confirmations;
    bool valid, ready;
    uint16_t advantage_pm; /* smallest model improvement across ALL contexts */
} iqf_shadow_t;

static inline void iqf_invalidate(iqf_shadow_t *s)
{
    s->valid = s->ready = false;
    s->confirmations = 0;
    s->pending = s->proposed = IQF_CURRENT;
    s->advantage_pm = 0;
}

static inline void iqf_observe(iqf_shadow_t *s, const uint8_t *raw, size_t bytes,
    uint64_t now_us, uint32_t epoch, uint32_t gain_epoch, bool valid)
{
    if (s->epoch != epoch || s->gain_epoch != gain_epoch || now_us < s->last_us) {
        iqf_invalidate(s);
        s->epoch = epoch;
        s->gain_epoch = gain_epoch;
    }
    s->last_us = now_us;
    if (!valid || !raw || bytes < 128u) { iqf_invalidate(s); return; }
    uint32_t score[IQF_CONTEXTS][IQF_CANDIDATES] = {{0}};
    /* At most 64 reads from the existing control snapshot. No new DMA copy. */
    for (unsigned sample = 0; sample < 64u; ++sample) {
        uint8_t byte = raw[(size_t)sample * (bytes - 1u) / 63u];
        for (unsigned c = 0; c < IQF_CONTEXTS; ++c)
            for (unsigned p = 0; p < IQF_CANDIDATES; ++p)
                score[c][p] += iqf_risk[c][p][byte];
    }
    unsigned best = IQF_CURRENT, advantage = 0;
    for (unsigned p = 0; p < IQF_CANDIDATES; ++p) {
        if (p == IQF_CURRENT) continue;
        unsigned floor = 1000u;
        for (unsigned c = 0; c < IQF_CONTEXTS; ++c) {
            uint32_t a = score[c][IQF_CURRENT], b = score[c][p];
            unsigned improvement = a > b ? (unsigned)((a - b) * 1000u / (a ? a : 1u)) : 0u;
            if (improvement < floor) floor = improvement;
        }
        if (floor > advantage) { best = p; advantage = floor; }
    }
    s->valid = true;
    ++s->windows;
    s->proposed = (uint8_t)best;
    s->advantage_pm = (uint16_t)advantage;
    s->ready = false;
    if (best == IQF_CURRENT || advantage < 100u) {
        s->pending = IQF_CURRENT;
        s->confirmations = 0;
        return;
    }
    if (s->pending != best || !s->confirmations) {
        s->pending = (uint8_t)best;
        s->confirmations = 1;
        s->since_us = now_us;
    } else if (s->confirmations < 255u) ++s->confirmations;
    s->ready = s->confirmations >= 3u && now_us - s->since_us >= 2000000u;
}
