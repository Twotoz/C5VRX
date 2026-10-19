/* C5VRX-5 by Twotoz and the C5VRX contributors: end-to-end video quality
 * (pure logic, host test test/test_video_quality.c).
 *
 * Every adaptive choice used to be judged on a substitute: DC against noise,
 * noise bandwidth, a model run on IQ without a carrier. Those cannot see a
 * sampling phase gone wrong, a profile that does not fit the VTX, or anything
 * nobody thought of. The demodulated video can: on the sync tips and the back
 * porch it is flat by definition, so its spread there is the noise of the
 * picture itself, whatever caused it. This sums the AFC's per-window sums
 * (afc2_result_t) over a period into
 *   snr_x10        20 log10(|porch - sync| / spread) x10 over every line found;
 *   lines_x100     sync lines found per measured window x100 (a 4096-sample
 *                  window spans ~1.6 lines; a picture lost in noise or DC
 *                  finds none, edge snapshots 2026-10-19).
 * A period without measured windows is unknown, not bad. */
#pragma once
#include "afc.h"

typedef struct {
    int64_t sync_sum, sync_sq, porch_sum, porch_sq;
    uint32_t sync_n, porch_n, lines, windows;
    int64_t start_us;
} vq_acc_t;

typedef struct {
    int16_t snr_x10;        /* 0 when no line was found */
    uint16_t lines_x100;
    uint32_t windows, lines;
    bool known;             /* windows were measured in the period */
} vq_t;

#define VQ_PERIOD_US    1000000LL
#define VQ_MIN_WINDOWS  20u

static inline void vq_add(vq_acc_t *a, const afc2_result_t *r)
{
    ++a->windows;
    if (!r->lines) return;
    a->lines += r->lines;
    a->sync_sum += r->sync_sum; a->sync_sq += r->sync_sq; a->sync_n += r->sync_pairs;
    a->porch_sum += r->porch_sum; a->porch_sq += r->porch_sq; a->porch_n += r->porch_pairs;
}

/* At the end of a period: the result, and a fresh accumulator. Returns true
 * when a period closed. */
static inline bool vq_close(vq_acc_t *a, int64_t now_us, vq_t *out)
{
    if (!a->start_us) { a->start_us = now_us; return false; }
    if (now_us - a->start_us < VQ_PERIOD_US) return false;
    vq_t v = {0};
    v.windows = a->windows;
    v.lines = a->lines;
    v.known = a->windows >= VQ_MIN_WINDOWS;
    v.lines_x100 = a->windows ? (uint16_t)(a->lines * 100u / a->windows) : 0;
    v.snr_x10 = afc2_snr_x10(a->sync_sum, a->sync_sq, a->sync_n, a->porch_sum, a->porch_sq, a->porch_n);
    *out = v;
    *a = (vq_acc_t){.start_us = now_us};
    return true;
}
