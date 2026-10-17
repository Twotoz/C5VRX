/* C5VRX by Twotoz/contributors: long-baseline camera period for holdover.
 * Task-side integer logic on the camera-phase guard's H trims (20 MHz).
 *
 * The holdover period came from the probe's 32..64-line fit, quantised to
 * 1/64 tick per halfline: up to 0.0156 tick/half, ~24 us/s of drift once
 * the camera is gone (a full line in ~2.6 s of static). While the camera is
 * aligned, every guard trim is the phase the grid lost against it, so
 * (sum of trims + residual) / elapsed halflines over >= 1 s is the period
 * error. Window phase noise of +-10 ticks over 31,000 halves resolves
 * ~3e-4 tick/half; the iterator stores the period as whole + num/SCP_DEN.
 * Any re-anchor (yield, takeover, standard change) restarts the window.
 * Evidence is timing only; monitor behaviour is not established. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

#define SCP_DEN 50000u           /* period fraction: 1/50000 tick per halfline */
#define SCP_MIN_HALVES 31000u    /* ~1 s PAL/NTSC before an estimate */
#define SCP_MIN_ALIGNED 2000u    /* aligned camera H pulses in that window */
#define SCP_MAX_PPM 200u         /* bound against the window's start period */

typedef struct {
    uint64_t period_q;           /* halfline, 1/SCP_DEN ticks */
    uint64_t start_half;
    int64_t trims;               /* ticks the grid was moved toward the camera */
    int32_t start_residual;      /* camera - grid at the window start */
    uint32_t aligned, updates, rejected;
    bool active;
} scp_t;

/* Period of the running iterator: whole + increment/denominator ticks. */
static inline uint64_t scp_period_q(uint32_t whole, uint32_t increment, uint32_t denominator)
{
    return (uint64_t)whole * SCP_DEN + ((uint64_t)increment * SCP_DEN + denominator / 2u) / denominator;
}

static inline void scp_start(scp_t *p, uint64_t period_q, uint64_t half, int32_t residual)
{
    uint32_t updates = p->updates, rejected = p->rejected;
    *p = (scp_t){.period_q = period_q, .start_half = half, .start_residual = residual,
                 .active = period_q != 0u};
    p->updates = updates; p->rejected = rejected;
}

static inline void scp_aligned(scp_t *p) { if (p->active && p->aligned != UINT32_MAX) ++p->aligned; }
static inline void scp_trim(scp_t *p, int32_t ticks) { if (p->active) p->trims += ticks; }

/* After an aligned pulse at `half` with `residual` ticks (camera - grid):
 * true and a new halfline period once the window is long and well
 * populated. The window then restarts on the new period. */
static inline bool scp_estimate(scp_t *p, uint64_t half, int32_t residual, uint64_t *period_q)
{
    if (!p->active || half <= p->start_half) return false;
    uint64_t halves = half - p->start_half;
    if (halves < SCP_MIN_HALVES) return false;
    if (p->aligned < SCP_MIN_ALIGNED) {           /* sparse camera: keep waiting, bounded */
        if (halves > 4u * SCP_MIN_HALVES) scp_start(p, p->period_q, half, residual);
        return false;
    }
    int64_t phase = p->trims + residual - p->start_residual; /* camera later => period too short */
    int64_t delta = (phase * (int64_t)SCP_DEN + (phase >= 0 ? 1 : -1) * (int64_t)(halves / 2u)) /
                    (int64_t)halves;
    int64_t limit = (int64_t)(p->period_q * SCP_MAX_PPM / 1000000u);
    if (delta > limit || delta < -limit) {
        ++p->rejected;
        scp_start(p, p->period_q, half, residual);
        return false;
    }
    *period_q = (uint64_t)((int64_t)p->period_q + delta);
    ++p->updates;
    scp_start(p, *period_q, half, residual);
    return true;
}
