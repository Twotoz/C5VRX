/* C5VRX by Twotoz/contributors: sampled sync-loss raster policy, not AFC lock. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#define VIDEO_LOSS_US 8000u
#define VIDEO_LOSS_WINDOWS 4u
#define VIDEO_RECOVERY_GRACE_US 12000u
typedef struct { uint32_t context; uint64_t last_sync, grace_until; unsigned bad; bool initialized; } video_loss_t;
static inline bool video_loss_update(video_loss_t *p, uint32_t context, bool eligible,
                                     bool observed, bool sync, uint64_t now, bool active)
{
    if (!p->initialized || p->context != context || !eligible) {
        *p = (video_loss_t){.context=context, .last_sync=now, .initialized=true};
        return active; /* other owners keep their picture; no loss inference */
    }
    if (!observed) return active; /* failed copies are unknown, not missing sync */
    if (sync) {
        p->last_sync = now;
        p->bad = 0;
        if (active) p->grace_until = now + VIDEO_RECOVERY_GRACE_US;
        return false; /* first confirmed camera sync, no AFC-lock wait */
    }
    if (p->bad < VIDEO_LOSS_WINDOWS) ++p->bad;
    return active || (p->bad >= VIDEO_LOSS_WINDOWS && now >= p->grace_until &&
                      now - p->last_sync >= VIDEO_LOSS_US);
}
