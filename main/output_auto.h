/* C5VRX-5 by Twotoz and the C5VRX contributors: evidence for OUTPUT AUTO
 * (output_level.h). The deviation estimator's accepted windows (burst or
 * line-timing confirmed, healthy gain, C/N >= 8 dB) also give the porch and
 * the white peak; this keeps the last batch of them per measurement cycle.
 * Pure; host test test/test_output_auto.c. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "output_level.h"

#define OUTPUT_AUTO_WINDOWS 96u   /* = DEV_BATCH x DEV_BATCHES */

typedef struct {
    int32_t porch[OUTPUT_AUTO_WINDOWS], white[OUTPUT_AUTO_WINDOWS];
    unsigned n, next;
    int32_t white_session;        /* highest white extent seen this channel session */
} output_auto_obs_t;

/* New measurement cycle; the session white peak is kept. */
static inline void output_auto_obs_cycle(output_auto_obs_t *o) { o->n = o->next = 0; }
/* New channel session (another VTX may be there). */
static inline void output_auto_obs_reset(output_auto_obs_t *o) { memset(o, 0, sizeof(*o)); }

static inline void output_auto_obs_push(output_auto_obs_t *o, int32_t porch_khz, int32_t white_ext_khz)
{
    o->porch[o->next] = porch_khz;
    o->white[o->next] = white_ext_khz > 0 ? white_ext_khz : 0;
    o->next = (o->next + 1u) % OUTPUT_AUTO_WINDOWS;
    if (o->n < OUTPUT_AUTO_WINDOWS) ++o->n;
}

static inline int32_t output_auto_rank(const int32_t *src, unsigned n, unsigned pct)
{
    int32_t v[OUTPUT_AUTO_WINDOWS];
    memcpy(v, src, n * sizeof(v[0]));
    for (unsigned a = 1; a < n; ++a)
        for (unsigned b = a; b > 0 && v[b - 1] > v[b]; --b) { int32_t t = v[b]; v[b] = v[b - 1]; v[b - 1] = t; }
    return v[(n - 1u) * pct / 100u];
}

/* A decided deviation: the AUTO target from this cycle's windows. The
 * white cap uses the 90th percentile of per-window peaks (a few noise or
 * chroma spikes do not shrink the picture) and only ever tightens within a
 * channel session, so a dark scene cannot raise the gain until a bright
 * one clips. Returns false without enough windows. */
static inline bool output_auto_obs_decide(output_auto_obs_t *o, int dev_x100, int32_t sync_khz_per_dev,
                                          output_auto_t *out)
{
    if (dev_x100 <= 0 || o->n < OUTPUT_AUTO_WINDOWS / 3u) return false;
    const int32_t porch = output_auto_rank(o->porch, o->n, 50u);
    const int32_t white = output_auto_rank(o->white, o->n, 90u);
    if (white > o->white_session) o->white_session = white;
    *out = output_auto_target((int)((int64_t)dev_x100 * sync_khz_per_dev / 100), (int)porch,
                              (int)o->white_session);
    return true;
}

/* Before this boot/channel measured its VTX: the stored deviation (a hint,
 * not a VTX identity) at the AFC porch target, else the nominal default. */
static inline output_auto_t output_auto_prior(int stored_dev_x100, int32_t sync_khz_per_dev)
{
    if (stored_dev_x100 <= 0) return OUTPUT_AUTO_DEFAULT;
    return output_auto_target((int)((int64_t)stored_dev_x100 * sync_khz_per_dev / 100), -1000, 0);
}
