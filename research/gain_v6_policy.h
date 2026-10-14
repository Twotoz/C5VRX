/* C5VRX by Twotoz and contributors: experimental Direct Gain V6 policy.
 * Extends main/gain.c V5; research only, no live caller or hardware writes.
 * See docs/DIRECT_GAIN_V6.md for evidence and integration prerequisites. */
#pragma once
#include "gain.h"
#include "dc_trim.h"
#ifndef DG6_EXPERIMENTAL_ENABLED
#define DG6_EXPERIMENTAL_ENABLED 0
#endif
#define DG6_MAX_AGE_US 2000u
#define DG6_MIN_SETTLE_US 500u
#define DG6_VALIDATE_US 10000u
#define DG6_COOLDOWN_US 200000u
#define DG6_FADE_US 10000u
typedef enum { DG6_UNKNOWN, DG6_CARRIER, DG6_NOISE, DG6_STUCK, DG6_FADE, DG6_OVERLOAD } dg6_class_t;
typedef enum { DG6_V5, DG6_HOLD, DG6_DIAGNOSE_DC, DG6_APPLY, DG6_ROLLBACK, DG6_SAFETY } dg6_action_t;
typedef enum { DG6_DISABLED, DG6_STALE, DG6_CONTEXT, DG6_DC_UNKNOWN, DG6_UNOBSERVABLE,
               DG6_HEALTHY, DG6_FADE_HOLD, DG6_MODEL, DG6_COOLDOWN, DG6_VERIFY,
               DG6_IMPROVED, DG6_HARMFUL, DG6_TIMEOUT, DG6_PROPOSAL, DG6_CLIPPING } dg6_reason_t;
typedef struct { dc_trim_context_t chain; int dc_pair[2]; } dg6_context_t;
typedef struct {
    dg_observation_t o;
    dg6_context_t measured, expected;
    uint64_t now_us, last_write_us;
    uint16_t settle_us;
    /* Independent evidence: never populate from coherence/envelope alone.
     * sync_confirmed must exclude flywheel edits. noise_confirmed must not
     * mean merely 'no sync'. dc_verified includes matched actual pair AND
     * a qualified residual/floor result, not just 'row exists'. */
    bool owner, dc_verified, sync_confirmed, noise_confirmed, map_context_verified;
    uint32_t actual_packed;
} dg6_input_t;
typedef struct { dg6_action_t action; dg6_class_t classification; dg6_reason_t reason; uint8_t target; } dg6_decision_t;
typedef struct {
    bool enabled, awaiting_apply, pending;
    uint8_t prior, target, bad_windows;
    dg6_class_t before_class;
    uint8_t penalty[DG_STATES];
    dg_observation_t before;
    dg6_context_t pending_context, carrier_context;
    uint64_t applied_us, last_carrier_us, cooldown_until_us, last_validation_us;
} dg6_t;
static inline void dg6_init(dg6_t *s) { memset(s, 0, sizeof(*s)); s->enabled = DG6_EXPERIMENTAL_ENABLED != 0; }
static inline bool dg6_same(const dg6_context_t *a, const dg6_context_t *b)
{
    return dc_trim_same(&a->chain, &b->chain) && a->dc_pair[0] == b->dc_pair[0] && a->dc_pair[1] == b->dc_pair[1];
}
static inline bool dg6_band(const dg_observation_t *o)
{
    return o->p50 >= 13 && o->p50 <= 32 && o->p95 <= 65 && o->clip_pm < 20 && o->origin_pm <= 250;
}
static inline unsigned dg6_error(const dg_observation_t *o)
{
    return o->p50 < 13 ? 13u - o->p50 : o->p50 > 32 ? o->p50 - 32u : 0;
}
static inline bool dg6_fresh(const dg6_input_t *in)
{
    uint64_t guard = in->settle_us > DG6_MIN_SETTLE_US ? in->settle_us : DG6_MIN_SETTLE_US;
    return in->owner && in->o.observed_us && in->now_us >= in->o.observed_us &&
           in->now_us - in->o.observed_us <= DG6_MAX_AGE_US && in->o.observed_us >= in->last_write_us &&
           in->o.observed_us - in->last_write_us >= guard && dg6_same(&in->measured, &in->expected) &&
           in->actual_packed == in->expected.chain.packed_gain;
}
static inline dg6_class_t dg6_classify(const dg6_t *s, const dg6_input_t *in)
{
    if (!dg6_fresh(in)) return DG6_UNKNOWN;
    if (in->o.clip_pm >= 100 || in->o.p95 >= 95) return DG6_OVERLOAD;
    if (dg_stuck(&in->o)) return DG6_STUCK;
    if (s->last_carrier_us && in->o.observed_us >= s->last_carrier_us &&
        in->o.observed_us - s->last_carrier_us <= DG6_FADE_US &&
        dg6_same(&in->measured, &s->carrier_context) && in->o.p50 < 13) return DG6_FADE;
    if (!in->dc_verified) return DG6_UNKNOWN;
    if (in->sync_confirmed && in->o.span >= 3) return DG6_CARRIER;
    if (in->noise_confirmed && in->o.span >= 3) return DG6_NOISE;
    return DG6_UNKNOWN;
}
/* Call after the sole gain owner verifies the proposed actual tuple and
 * applies that row's DC. Failure never advances policy ahead of hardware.
 * Rollback must pass the same real actuator/readback contract as any write. */
static inline bool dg6_applied(dg6_t *s, bool verified, const dg6_context_t *actual, uint64_t when_us)
{
    if (!s->awaiting_apply) return false;
    s->awaiting_apply = false;
    if (!verified || !actual || actual->chain.gain != s->target) {
        s->cooldown_until_us = when_us + DG6_COOLDOWN_US;
        return false;
    }
    s->pending = true; s->pending_context = *actual; s->applied_us = when_us;
    s->last_validation_us = 0; s->bad_windows = 0;
    return true;
}
static inline dg6_decision_t dg6_decide(dg6_t *s, const direct_gain_t *map,
                                        const dg6_input_t *in, uint8_t v5_proposal)
{
    uint8_t current = (uint8_t)in->expected.chain.gain;
    dg6_class_t c = dg6_classify(s, in);
    dg6_decision_t d = {DG6_HOLD, c, DG6_STALE, current};
    if (!s->enabled) { d.action = DG6_V5; d.reason = DG6_DISABLED; d.target = v5_proposal; return d; }
    if (!dg6_fresh(in)) return d;
    if (current >= DG_STATES || current > map->table.max_index) { d.reason = DG6_CONTEXT; return d; }
    /* Preserve the actual V5 sustained-overload/burst gate. A single clipped
     * probe cannot bypass its 1-ms persistence requirement. */
    if (c == DG6_OVERLOAD) {
        d.reason = DG6_CLIPPING;
        if (v5_proposal < current) { d.action = DG6_SAFETY; d.target = v5_proposal; s->pending = s->awaiting_apply = false; }
        return d;
    }
    if (s->pending) {
        if (!dg6_same(&in->measured, &s->pending_context)) {
            s->pending = false; s->cooldown_until_us = in->now_us + DG6_COOLDOWN_US;
            d.reason = DG6_CONTEXT; return d; /* never undo a new owner's context */
        }
        if (in->o.observed_us <= s->applied_us || in->o.observed_us == s->last_validation_us) { d.reason = DG6_VERIFY; return d; }
        s->last_validation_us = in->o.observed_us;
        bool good = c == s->before_class && in->dc_verified && !dg_stuck(&in->o) && in->o.clip_pm < 20 &&
                    dg6_error(&in->o) < dg6_error(&s->before) && in->o.span >= 3;
        if (good) { s->pending = false; s->cooldown_until_us = in->now_us + 20000u; d.reason = DG6_IMPROVED; return d; }
        bool bad = dg_stuck(&in->o) || in->o.clip_pm > s->before.clip_pm + 30u ||
                   dg6_error(&in->o) > dg6_error(&s->before) + 3u;
        if (bad && s->bad_windows < 2) ++s->bad_windows;
        bool timed_out = in->now_us >= s->applied_us && in->now_us - s->applied_us >= DG6_VALIDATE_US;
        if (s->bad_windows >= 2 || timed_out) {
            s->pending = false; s->cooldown_until_us = in->now_us + DG6_COOLDOWN_US;
            if (s->penalty[s->target] < 255) ++s->penalty[s->target];
            d.action = DG6_ROLLBACK; d.target = s->prior; d.reason = timed_out ? DG6_TIMEOUT : DG6_HARMFUL;
        } else d.reason = DG6_VERIFY;
        return d;
    }
    if (s->awaiting_apply) { d.reason = DG6_VERIFY; return d; }
    if (c == DG6_STUCK) { d.action = DG6_DIAGNOSE_DC; d.reason = DG6_UNOBSERVABLE; return d; }
    if (c == DG6_UNKNOWN) { d.reason = DG6_DC_UNKNOWN; return d; }
    if (c == DG6_FADE) { d.reason = DG6_FADE_HOLD; return d; }
    if (c == DG6_CARRIER && dg6_band(&in->o)) {
        s->last_carrier_us = in->o.observed_us; s->carrier_context = in->measured;
        d.reason = DG6_HEALTHY; return d;
    }
    if (in->now_us < s->cooldown_until_us) { d.reason = DG6_COOLDOWN; return d; }
    if (v5_proposal == current) { d.reason = DG6_HEALTHY; return d; }
    if (!in->map_context_verified || v5_proposal >= DG_STATES || v5_proposal > map->table.max_index || map->confidence[current] < 3 ||
        map->confidence[v5_proposal] < 3 || !map->relative_power_q10[current] || !map->relative_power_q10[v5_proposal] ||
        map->uncertainty_pm[current] + map->uncertainty_pm[v5_proposal] > 350u || s->penalty[v5_proposal] >= 2) {
        d.reason = DG6_MODEL; return d;
    }
    uint64_t ratio = (uint64_t)map->relative_power_q10[v5_proposal] * 1024u / map->relative_power_q10[current];
    uint64_t p95 = in->o.p95 * ratio * (1000u + map->uncertainty_pm[current] + map->uncertainty_pm[v5_proposal]) / 1024000u;
    unsigned predicted = (unsigned)(in->o.p50 * ratio / 1024u);
    unsigned error = predicted < 13 ? 13u - predicted : predicted > 32 ? predicted - 32u : 0;
    if (p95 > 65 || error >= dg6_error(&in->o)) { d.reason = DG6_MODEL; return d; }
    s->before = in->o; s->before_class = c; s->prior = current; s->target = v5_proposal; s->awaiting_apply = true;
    d.action = DG6_APPLY; d.reason = DG6_PROPOSAL; d.target = v5_proposal;
    return d;
}
