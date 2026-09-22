#include "arc_v4_snap.h"

#include <stdbool.h>
#include <stddef.h>

/*
 * Timing is expressed in fast-observer samples.  The production observer is
 * currently ~6 ms/sample:
 *   SOFT      50 samples ~= 300 ms
 *   FAST      18 samples ~= 108 ms
 *   CRITICAL   2 samples ~= 12 ms
 *
 * These are handoff confirmation windows, not polling periods.  LOCK is still
 * observed every fast sample and performs zero PHY writes.
 */
#define SNAP_SOFT_CONFIRM_SAMPLES        50u
#define SNAP_FAST_CONFIRM_SAMPLES        18u
#define SNAP_CRITICAL_CONFIRM_SAMPLES     2u
#define SNAP_POST_WRITE_DISCARD_SAMPLES   5u
#define SNAP_VERIFY_GOOD_SAMPLES          3u
#define SNAP_VERIFY_FAST_SAMPLES          4u
#define SNAP_VERIFY_SOFT_SAMPLES         12u
#define SNAP_VERIFY_TIMEOUT_SAMPLES      30u
#define SNAP_RF_LIMIT_CONFIRM_SAMPLES    18u

/* Empirical hardware anchors from the current C5VRX walk/calibration data.
 * They are search/handoff nodes, not distance labels and not linear dB.
 * The top anchor is clamped against the active vendor-generated table. */
static const uint8_t k_snap_anchors[ARC_V4_SNAP_ANCHOR_COUNT] = {
    16u, 40u, 54u, 70u, 78u, 81u
};

static int iabs_i(int v) { return v < 0 ? -v : v; }

static int ema(int current, int sample, unsigned divisor)
{
    return current + (sample - current) / (int)divisor;
}

static uint8_t clamp_gain(const arc_v4_snap_t *snap, int gain)
{
    if (gain < 2) gain = 2;
    if (gain > snap->table.max_index) gain = snap->table.max_index;
    return (uint8_t)gain;
}

static void reset_filter(arc_v4_snap_t *snap)
{
    snap->primed = 0u;
    snap->fast_p = snap->slow_p = 0;
    snap->fast_q = snap->slow_q = 0;
    snap->fast_clip = snap->slow_clip = 0;
    snap->fast_origin = snap->slow_origin = 0;
    snap->fast_winding = snap->slow_winding = 0;
    snap->margin_score = 0;
}

static void update_filter(arc_v4_snap_t *snap,
                          const arc_v4_snap_observation_t *o)
{
    if (!snap->primed) {
        snap->primed = 1u;
        snap->fast_p = snap->slow_p = o->p_median;
        snap->fast_q = snap->slow_q = o->q_phase;
        snap->fast_clip = snap->slow_clip = o->clip_permille;
        snap->fast_origin = snap->slow_origin = o->origin_permille;
        snap->fast_winding = snap->slow_winding = o->winding_permille;
        return;
    }

    /* Fast ~= 1-2 observer periods of response, slow ~= ~150 ms of memory.
     * Integer EMA is deliberate: fixed cost and deterministic on ESP32-C5. */
    snap->fast_p = ema(snap->fast_p, o->p_median, 2u);
    snap->slow_p = ema(snap->slow_p, o->p_median, 24u);
    snap->fast_q = ema(snap->fast_q, o->q_phase, 2u);
    snap->slow_q = ema(snap->slow_q, o->q_phase, 24u);
    snap->fast_clip = ema(snap->fast_clip, o->clip_permille, 2u);
    snap->slow_clip = ema(snap->slow_clip, o->clip_permille, 24u);
    snap->fast_origin = ema(snap->fast_origin, o->origin_permille, 2u);
    snap->slow_origin = ema(snap->slow_origin, o->origin_permille, 24u);
    snap->fast_winding = ema(snap->fast_winding, o->winding_permille, 2u);
    snap->slow_winding = ema(snap->slow_winding, o->winding_permille, 24u);
}

static bool lock_has_usable_margin(const arc_v4_snap_t *snap)
{
    return snap->fast_clip <= 24 &&
           snap->fast_p >= 7 && snap->fast_p <= 38 &&
           snap->fast_q >= 58 &&
           snap->fast_origin <= 480 &&
           snap->fast_winding < 330;
}

static arc_v4_snap_urgency_t weak_urgency(
    arc_v4_snap_t *snap, const arc_v4_snap_observation_t *o)
{
    const bool hard_raw =
        o->clip_permille <= 8 &&
        o->p_median <= 4 &&
        o->q_phase < 20 &&
        o->origin_permille >= 750;

    if (hard_raw ||
        (snap->fast_p <= 5 && snap->fast_q < 38 &&
         snap->fast_origin >= 600))
        return ARC_V4_SNAP_CRITICAL;

    const bool weak_trend =
        snap->fast_p <= snap->slow_p - 3 ||
        snap->fast_q <= snap->slow_q - 10 ||
        snap->fast_origin >= snap->slow_origin + 80 ||
        snap->fast_winding >= snap->slow_winding + 90;

    if ((snap->fast_p <= 8 && snap->fast_q < 68) ||
        snap->fast_q < 55 ||
        snap->fast_origin >= 430)
        return weak_trend ? ARC_V4_SNAP_FAST : ARC_V4_SNAP_SOFT;

    if (weak_trend &&
        (snap->fast_p <= 12 ||
         snap->fast_q < 86 ||
         snap->fast_origin >= 180))
        return ARC_V4_SNAP_SOFT;

    return ARC_V4_SNAP_URGENCY_NONE;
}

static arc_v4_snap_urgency_t strong_urgency(
    arc_v4_snap_t *snap, const arc_v4_snap_observation_t *o)
{
    if (o->clip_permille >= 100 || o->p_median >= 60 ||
        snap->fast_clip >= 90 || snap->fast_p >= 55)
        return ARC_V4_SNAP_CRITICAL;

    const bool strong_trend =
        snap->fast_p >= snap->slow_p + 6 ||
        snap->fast_clip >= snap->slow_clip + 18;

    if (snap->fast_clip >= 32 || snap->fast_p > 45)
        return strong_trend ? ARC_V4_SNAP_FAST : ARC_V4_SNAP_SOFT;

    if (strong_trend &&
        (snap->fast_clip >= 12 || snap->fast_p >= 34))
        return ARC_V4_SNAP_SOFT;

    return ARC_V4_SNAP_URGENCY_NONE;
}

static unsigned confirm_samples(arc_v4_snap_urgency_t urgency)
{
    switch (urgency) {
    case ARC_V4_SNAP_CRITICAL: return SNAP_CRITICAL_CONFIRM_SAMPLES;
    case ARC_V4_SNAP_FAST:     return SNAP_FAST_CONFIRM_SAMPLES;
    case ARC_V4_SNAP_SOFT:     return SNAP_SOFT_CONFIRM_SAMPLES;
    default:                   return 0u;
    }
}

static int first_anchor_above(const arc_v4_snap_t *snap, uint8_t gain)
{
    for (unsigned i = 0; i < ARC_V4_SNAP_ANCHOR_COUNT; ++i) {
        uint8_t a = clamp_gain(snap, k_snap_anchors[i]);
        if (a > gain) return (int)i;
    }
    return -1;
}

static int first_anchor_below(const arc_v4_snap_t *snap, uint8_t gain)
{
    for (int i = (int)ARC_V4_SNAP_ANCHOR_COUNT - 1; i >= 0; --i) {
        uint8_t a = clamp_gain(snap, k_snap_anchors[i]);
        if (a < gain) return i;
    }
    return -1;
}

static uint8_t anchor_with_hops(const arc_v4_snap_t *snap,
                                arc_v4_snap_direction_t direction,
                                unsigned hops,
                                const arc_v4_snap_observation_t *o)
{
    if (hops < 1u) hops = 1u;

    if (direction == ARC_V4_SNAP_WEAKER) {
        int idx = first_anchor_above(snap, snap->gain);
        if (idx < 0) return snap->gain;

        /* A true quantizer-collapse signature is allowed to skip farther than
         * an ordinary pre-cliff handoff.  This remains bounded by measured
         * anchors and the vendor-generated table ceiling. */
        if (o->p_median <= 3 && o->q_phase <= 15 &&
            o->origin_permille >= 800)
            hops = hops < 3u ? 3u : hops;

        idx += (int)hops - 1;
        if (idx >= (int)ARC_V4_SNAP_ANCHOR_COUNT)
            idx = (int)ARC_V4_SNAP_ANCHOR_COUNT - 1;
        return clamp_gain(snap, k_snap_anchors[idx]);
    }

    if (direction == ARC_V4_SNAP_STRONGER) {
        int idx = first_anchor_below(snap, snap->gain);
        if (idx < 0) return snap->gain;

        if (o->clip_permille >= 100 || o->p_median >= 60)
            hops = hops < 2u ? 2u : hops;

        idx -= (int)hops - 1;
        if (idx < 0) idx = 0;
        return clamp_gain(snap, k_snap_anchors[idx]);
    }

    return snap->gain;
}

static uint8_t choose_target(const arc_v4_snap_t *snap,
                             arc_v4_snap_direction_t direction,
                             arc_v4_snap_urgency_t urgency,
                             const arc_v4_snap_observation_t *o)
{
    unsigned hops = 1u;
    if (urgency == ARC_V4_SNAP_CRITICAL) hops = 2u;
    return anchor_with_hops(snap, direction, hops, o);
}

static uint8_t commit_handoff(arc_v4_snap_t *snap, uint8_t target)
{
    target = clamp_gain(snap, target);
    if (target == snap->gain) return snap->gain;

    snap->gain = target;
    ++snap->epoch;
    ++snap->handoffs;
    snap->state = ARC_V4_SNAP_VERIFY;
    snap->discard_samples = SNAP_POST_WRITE_DISCARD_SAMPLES;
    snap->verify_samples = 0u;
    snap->verify_good_samples = 0u;
    snap->pending_samples = 0u;
    snap->pending_direction = ARC_V4_SNAP_DIR_NONE;
    snap->pending_urgency = ARC_V4_SNAP_URGENCY_NONE;
    snap->rf_limit_samples = 0u;
    reset_filter(snap);
    return snap->gain;
}

static void update_margin_score(arc_v4_snap_t *snap)
{
    /* Diagnostic 0..1000 score only; decisions use the raw components above. */
    int score = 1000;
    if (snap->fast_p < 12) score -= (12 - snap->fast_p) * 35;
    if (snap->fast_q < 90) score -= (90 - snap->fast_q) * 8;
    if (snap->fast_origin > 100) score -= (snap->fast_origin - 100) / 2;
    if (snap->fast_clip > 8) score -= (snap->fast_clip - 8) * 2;
    if (snap->fast_p > 38) score -= (snap->fast_p - 38) * 18;
    if (score < 0) score = 0;
    if (score > 1000) score = 1000;
    snap->margin_score = score;
}

void arc_v4_snap_reset(arc_v4_snap_t *snap,
                       const arc_gain_table_t *table,
                       uint8_t gain)
{
    *snap = (arc_v4_snap_t){0};
    snap->table = *table;
    snap->gain = clamp_gain(snap, gain);
    snap->state = ARC_V4_SNAP_VERIFY;
    snap->epoch = 1u;
    snap->discard_samples = SNAP_POST_WRITE_DISCARD_SAMPLES;
    reset_filter(snap);
}

uint8_t arc_v4_snap_tick(arc_v4_snap_t *snap,
                         const arc_v4_snap_observation_t *o)
{
    if (snap->discard_samples != 0u) {
        --snap->discard_samples;
        snap->state = ARC_V4_SNAP_VERIFY;
        return snap->gain;
    }

    update_filter(snap, o);
    update_margin_score(snap);

    arc_v4_snap_urgency_t weak = weak_urgency(snap, o);
    arc_v4_snap_urgency_t strong = strong_urgency(snap, o);

    arc_v4_snap_direction_t direction = ARC_V4_SNAP_DIR_NONE;
    arc_v4_snap_urgency_t urgency = ARC_V4_SNAP_URGENCY_NONE;

    /* Hard clipping has priority. Otherwise pick the stronger directional
     * evidence. Ties favor WEAKER because losing Q4 phase causes the abrupt
     * video cliff this controller exists to avoid. */
    if (strong == ARC_V4_SNAP_CRITICAL) {
        direction = ARC_V4_SNAP_STRONGER;
        urgency = strong;
    } else if (weak != ARC_V4_SNAP_URGENCY_NONE &&
               weak >= strong) {
        direction = ARC_V4_SNAP_WEAKER;
        urgency = weak;
    } else if (strong != ARC_V4_SNAP_URGENCY_NONE) {
        direction = ARC_V4_SNAP_STRONGER;
        urgency = strong;
    }

    snap->last_direction = direction;
    snap->last_urgency = urgency;

    if (snap->state == ARC_V4_SNAP_VERIFY) {
        ++snap->verify_samples;

        if (lock_has_usable_margin(snap) &&
            direction == ARC_V4_SNAP_DIR_NONE) {
            if (++snap->verify_good_samples >= SNAP_VERIFY_GOOD_SAMPLES) {
                snap->state = ARC_V4_SNAP_LOCK;
                snap->verify_samples = 0u;
                snap->verify_good_samples = 0u;
            }
            return snap->gain;
        }

        snap->verify_good_samples = 0u;

        unsigned needed = urgency >= ARC_V4_SNAP_FAST ?
                          SNAP_VERIFY_FAST_SAMPLES :
                          SNAP_VERIFY_SOFT_SAMPLES;

        if (direction != ARC_V4_SNAP_DIR_NONE &&
            snap->verify_samples >= needed) {
            uint8_t target = choose_target(snap, direction, urgency, o);
            if (target != snap->gain)
                return commit_handoff(snap, target);
        }

        /* Ambiguous post-write state must not create endless actuator chatter.
         * Return to observation-only LOCK and let a fresh margin crossing earn
         * the next handoff normally. */
        if (snap->verify_samples >= SNAP_VERIFY_TIMEOUT_SAMPLES) {
            snap->state = ARC_V4_SNAP_LOCK;
            snap->verify_samples = 0u;
        }
        return snap->gain;
    }

    /* At the vendor ceiling, ordinary low-margin windows are still useful
     * video and must not be mislabeled RF_LIMIT. Only sustained critical
     * starvation promotes the explicit limit state. */
    if (direction == ARC_V4_SNAP_WEAKER &&
        snap->gain >= snap->table.max_index) {
        if (urgency == ARC_V4_SNAP_CRITICAL) {
            if (++snap->rf_limit_samples >= SNAP_RF_LIMIT_CONFIRM_SAMPLES)
                snap->state = ARC_V4_SNAP_RF_LIMIT;
        } else {
            snap->rf_limit_samples = 0u;
            if (snap->state == ARC_V4_SNAP_RF_LIMIT)
                snap->state = ARC_V4_SNAP_LOCK;
        }
        return snap->gain;
    }

    if (snap->state == ARC_V4_SNAP_RF_LIMIT &&
        direction != ARC_V4_SNAP_WEAKER) {
        snap->state = ARC_V4_SNAP_LOCK;
        snap->rf_limit_samples = 0u;
    }

    if (direction == ARC_V4_SNAP_DIR_NONE) {
        snap->pending_direction = ARC_V4_SNAP_DIR_NONE;
        snap->pending_urgency = ARC_V4_SNAP_URGENCY_NONE;
        snap->pending_samples = 0u;
        if (snap->state != ARC_V4_SNAP_RF_LIMIT)
            snap->state = ARC_V4_SNAP_LOCK;
        return snap->gain;
    }

    /* If the same directional evidence persists, keep accumulated confidence.
     * Urgency may upgrade without resetting the timer. A direction reversal
     * starts a fresh handoff candidate; there is deliberately no fixed
     * one-second reversal lockout. */
    if (direction != snap->pending_direction) {
        snap->pending_direction = direction;
        snap->pending_urgency = urgency;
        snap->pending_samples = 1u;
    } else {
        if (urgency > snap->pending_urgency)
            snap->pending_urgency = urgency;
        if (snap->pending_samples < 1000u)
            ++snap->pending_samples;
    }

    snap->state = ARC_V4_SNAP_PREHANDOFF;

    unsigned needed = confirm_samples(snap->pending_urgency);
    if (snap->pending_samples < needed)
        return snap->gain;

    uint8_t target = choose_target(snap, snap->pending_direction,
                                   snap->pending_urgency, o);
    if (target == snap->gain) {
        snap->pending_samples = 0u;
        snap->pending_direction = ARC_V4_SNAP_DIR_NONE;
        snap->pending_urgency = ARC_V4_SNAP_URGENCY_NONE;
        snap->state = ARC_V4_SNAP_LOCK;
        return snap->gain;
    }

    return commit_handoff(snap, target);
}

const char *arc_v4_snap_state_name(arc_v4_snap_state_t state)
{
    switch (state) {
    case ARC_V4_SNAP_PREHANDOFF: return "PRE-HANDOFF";
    case ARC_V4_SNAP_VERIFY:     return "VERIFY";
    case ARC_V4_SNAP_RF_LIMIT:   return "RF_LIMIT";
    default:                     return "LOCK";
    }
}

const char *arc_v4_snap_direction_name(arc_v4_snap_direction_t direction)
{
    switch (direction) {
    case ARC_V4_SNAP_WEAKER:   return "WEAKER";
    case ARC_V4_SNAP_STRONGER: return "STRONGER";
    default:                   return "NONE";
    }
}

const char *arc_v4_snap_urgency_name(arc_v4_snap_urgency_t urgency)
{
    switch (urgency) {
    case ARC_V4_SNAP_CRITICAL: return "CRITICAL";
    case ARC_V4_SNAP_FAST:     return "FAST";
    case ARC_V4_SNAP_SOFT:     return "SOFT";
    default:                   return "NONE";
    }
}
