#include "arc_v4_glide.h"

#include <stdbool.h>

/*
 * Fast observer cadence is ~6 ms.
 *
 * Normal weak-side GLIDE starts after 3 consecutive edge samples (~18 ms).
 * Strong-side GLIDE is intentionally slower (8 samples ~= 48 ms) because too
 * much gain costs less range than too little gain.
 *
 * After every physical write we ignore two fast windows (~12 ms) and verify
 * fresh Q4.  No 500 ms fixed settle and no one-second reversal timer.
 */
#define GLIDE_WEAK_CONFIRM_SAMPLES      3u
#define GLIDE_STRONG_CONFIRM_SAMPLES    8u
#define GLIDE_POST_WRITE_DISCARD        2u
#define GLIDE_VERIFY_SAMPLES            3u
#define GLIDE_RF_LIMIT_CONFIRM         12u

static const uint8_t k_glide_anchors[ARC_V4_GLIDE_ANCHOR_COUNT] = {
    16u, 40u, 54u, 70u, 78u, 81u
};

static int ema(int current, int sample, unsigned divisor)
{
    return current + (sample - current) / (int)divisor;
}

static uint8_t clamp_gain(const arc_v4_glide_t *g, int gain)
{
    if (gain < 2) gain = 2;
    if (gain > g->table.max_index) gain = g->table.max_index;
    return (uint8_t)gain;
}

static void filter_reset(arc_v4_glide_t *g)
{
    g->primed = 0u;
    g->baseline_valid = 0u;
    g->fast_p = g->slow_p = 0;
    g->fast_q = g->slow_q = 0;
    g->fast_clip = g->slow_clip = 0;
    g->fast_origin = g->slow_origin = 0;
    g->fast_winding = g->slow_winding = 0;
    g->base_p = g->base_q = g->base_clip = 0;
    g->base_origin = g->base_winding = 0;
    g->weak_votes = g->strong_votes = 0;
    g->margin_score = 0;
}

static void update_filter(arc_v4_glide_t *g,
                          const arc_v4_glide_observation_t *o)
{
    if (!g->primed) {
        g->primed = 1u;
        g->fast_p = g->slow_p = o->p_median;
        g->fast_q = g->slow_q = o->q_phase;
        g->fast_clip = g->slow_clip = o->clip_permille;
        g->fast_origin = g->slow_origin = o->origin_permille;
        g->fast_winding = g->slow_winding = o->winding_permille;

        g->base_p = o->p_median;
        g->base_q = o->q_phase;
        g->base_clip = o->clip_permille;
        g->base_origin = o->origin_permille;
        g->base_winding = o->winding_permille;
        g->baseline_valid = 1u;
        return;
    }

    /* Fast follows ~1-2 samples. Slow tracks roughly ~100 ms. */
    g->fast_p = ema(g->fast_p, o->p_median, 2u);
    g->slow_p = ema(g->slow_p, o->p_median, 16u);
    g->fast_q = ema(g->fast_q, o->q_phase, 2u);
    g->slow_q = ema(g->slow_q, o->q_phase, 16u);
    g->fast_clip = ema(g->fast_clip, o->clip_permille, 2u);
    g->slow_clip = ema(g->slow_clip, o->clip_permille, 16u);
    g->fast_origin = ema(g->fast_origin, o->origin_permille, 2u);
    g->slow_origin = ema(g->slow_origin, o->origin_permille, 16u);
    g->fast_winding = ema(g->fast_winding, o->winding_permille, 2u);
    g->slow_winding = ema(g->slow_winding, o->winding_permille, 16u);
}

static bool hard_starved(const arc_v4_glide_observation_t *o)
{
    return o->clip_permille <= 8 &&
           o->p_median <= 4 &&
           o->q_phase < 20 &&
           o->origin_permille >= 750;
}

static bool hard_overload(const arc_v4_glide_observation_t *o)
{
    return o->clip_permille >= 100 || o->p_median >= 60;
}

static bool healthy_corridor(const arc_v4_glide_t *g)
{
    return g->fast_clip <= 20 &&
           g->fast_p >= 8 && g->fast_p <= 40 &&
           g->fast_q >= 60 &&
           g->fast_origin <= 450 &&
           g->fast_winding < 320;
}

static bool comfortable_corridor(const arc_v4_glide_t *g)
{
    return g->fast_clip <= 12 &&
           g->fast_p >= 11 && g->fast_p <= 32 &&
           g->fast_q >= 78 &&
           g->fast_origin <= 320 &&
           g->fast_winding < 280;
}

static bool weak_edge_absolute(const arc_v4_glide_t *g)
{
    return (g->fast_p <= 9 && g->fast_q < 72) ||
           g->fast_q < 58 ||
           g->fast_origin >= 400;
}

static bool strong_edge_absolute(const arc_v4_glide_t *g)
{
    return g->fast_clip >= 30 || g->fast_p > 45;
}

static void update_margin_score(arc_v4_glide_t *g)
{
    int score = 1000;
    if (g->fast_p < 12) score -= (12 - g->fast_p) * 35;
    if (g->fast_q < 90) score -= (90 - g->fast_q) * 7;
    if (g->fast_origin > 100) score -= (g->fast_origin - 100) / 2;
    if (g->fast_clip > 8) score -= (g->fast_clip - 8) * 2;
    if (g->fast_p > 38) score -= (g->fast_p - 38) * 18;
    if (score < 0) score = 0;
    if (score > 1000) score = 1000;
    g->margin_score = score;
}

static void update_votes(arc_v4_glide_t *g)
{
    if (!g->baseline_valid) {
        g->weak_votes = g->strong_votes = 0;
        return;
    }

    int weak = 0;
    if (g->fast_p <= g->base_p - 3 || g->fast_p <= g->slow_p - 2) ++weak;
    if (g->fast_q <= g->base_q - 8 || g->fast_q <= g->slow_q - 7) ++weak;
    if (g->fast_origin >= g->base_origin + 70 ||
        g->fast_origin >= g->slow_origin + 60) ++weak;
    if (g->fast_winding >= g->base_winding + 80 ||
        g->fast_winding >= g->slow_winding + 70) ++weak;

    int strong = 0;
    if (g->fast_p >= g->base_p + 6 || g->fast_p >= g->slow_p + 5) ++strong;
    if (g->fast_clip >= g->base_clip + 15 ||
        g->fast_clip >= g->slow_clip + 12) ++strong;
    if (g->fast_origin <= g->base_origin - 80 &&
        g->fast_p >= g->base_p + 4) ++strong;

    g->weak_votes = weak;
    g->strong_votes = strong;
}

static void baseline_follow_hold(arc_v4_glide_t *g)
{
    if (g->baseline_valid) return;

    /* Freeze one baseline for the complete HOLD epoch. If the user walks away
     * slowly, GLIDE must measure the accumulated loss of margin instead of
     * adapting the reference along with it. A fresh baseline is created only
     * after reset or a physical gain write. Stable-low startup still remains
     * HOLD because that low state becomes the epoch baseline itself. */
    g->base_p = g->fast_p;
    g->base_q = g->fast_q;
    g->base_clip = g->fast_clip;
    g->base_origin = g->fast_origin;
    g->base_winding = g->fast_winding;
    g->baseline_valid = 1u;
}

static int first_anchor_above(const arc_v4_glide_t *g)
{
    for (unsigned i = 0; i < ARC_V4_GLIDE_ANCHOR_COUNT; ++i) {
        uint8_t a = clamp_gain(g, k_glide_anchors[i]);
        if (a > g->gain) return (int)i;
    }
    return -1;
}

static int first_anchor_below(const arc_v4_glide_t *g)
{
    for (int i = (int)ARC_V4_GLIDE_ANCHOR_COUNT - 1; i >= 0; --i) {
        uint8_t a = clamp_gain(g, k_glide_anchors[i]);
        if (a < g->gain) return i;
    }
    return -1;
}

static uint8_t escape_target(const arc_v4_glide_t *g,
                             arc_v4_glide_direction_t direction,
                             const arc_v4_glide_observation_t *o)
{
    if (direction == ARC_V4_GLIDE_WEAKER) {
        int idx = first_anchor_above(g);
        if (idx < 0) return g->gain;

        /* Extreme collapse may skip one extra calibrated region. */
        if (o->p_median <= 2 && o->q_phase <= 8 &&
            o->origin_permille >= 900)
            ++idx;

        if (idx >= (int)ARC_V4_GLIDE_ANCHOR_COUNT)
            idx = (int)ARC_V4_GLIDE_ANCHOR_COUNT - 1;
        return clamp_gain(g, k_glide_anchors[idx]);
    }

    if (direction == ARC_V4_GLIDE_STRONGER) {
        int idx = first_anchor_below(g);
        if (idx < 0) return g->gain;
        if (o->clip_permille >= 200 || o->p_median >= 70)
            --idx;
        if (idx < 0) idx = 0;
        return clamp_gain(g, k_glide_anchors[idx]);
    }

    return g->gain;
}

static uint8_t write_gain(arc_v4_glide_t *g, uint8_t next,
                          arc_v4_glide_action_t action,
                          arc_v4_glide_direction_t direction)
{
    next = clamp_gain(g, next);
    if (next == g->gain) return g->gain;

    g->gain = next;
    ++g->epoch;
    ++g->writes;
    if (action == ARC_V4_GLIDE_ACTION_ESCAPE)
        ++g->escape_writes;

    g->last_action = action;
    g->direction = direction;
    g->state = ARC_V4_GLIDE_VERIFY;
    g->discard_samples = GLIDE_POST_WRITE_DISCARD;
    g->verify_samples = 0u;
    g->trend_samples = 0u;
    filter_reset(g);
    return g->gain;
}

static uint8_t glide_step(arc_v4_glide_t *g,
                          arc_v4_glide_direction_t direction)
{
    int delta;

    if (direction == ARC_V4_GLIDE_WEAKER) {
        /* Normal movement should feel continuous. Only a clearly thin edge
         * uses +4; ordinary early movement is +2. */
        delta = weak_edge_absolute(g) ? 4 : 2;
    } else {
        /* Downward motion is deliberately quieter and slower. */
        delta = strong_edge_absolute(g) ? -4 : -2;
    }

    return write_gain(g, clamp_gain(g, (int)g->gain + delta),
                      ARC_V4_GLIDE_ACTION_STEP, direction);
}

void arc_v4_glide_reset(arc_v4_glide_t *g,
                        const arc_gain_table_t *table,
                        uint8_t gain)
{
    *g = (arc_v4_glide_t){0};
    g->table = *table;
    g->gain = clamp_gain(g, gain);
    g->state = ARC_V4_GLIDE_HOLD;
    g->direction = ARC_V4_GLIDE_DIR_NONE;
    g->last_action = ARC_V4_GLIDE_ACTION_NONE;
    g->epoch = 1u;
    g->discard_samples = GLIDE_POST_WRITE_DISCARD;
    filter_reset(g);
}

uint8_t arc_v4_glide_tick(arc_v4_glide_t *g,
                          const arc_v4_glide_observation_t *o)
{
    if (g->discard_samples != 0u) {
        --g->discard_samples;
        return g->gain;
    }

    update_filter(g, o);
    update_margin_score(g);
    update_votes(g);

    /* ESCAPE is the only level-triggered path. Stable low P/Q does not glide,
     * but genuine Q4 collapse or major clipping must recover immediately. */
    if (hard_overload(o)) {
        uint8_t target = escape_target(g, ARC_V4_GLIDE_STRONGER, o);
        if (target != g->gain)
            return write_gain(g, target, ARC_V4_GLIDE_ACTION_ESCAPE,
                              ARC_V4_GLIDE_STRONGER);
    }

    if (hard_starved(o)) {
        if (g->gain >= g->table.max_index) {
            if (++g->rf_limit_samples >= GLIDE_RF_LIMIT_CONFIRM)
                g->state = ARC_V4_GLIDE_RF_LIMIT;
            return g->gain;
        }

        uint8_t target = escape_target(g, ARC_V4_GLIDE_WEAKER, o);
        if (target != g->gain)
            return write_gain(g, target, ARC_V4_GLIDE_ACTION_ESCAPE,
                              ARC_V4_GLIDE_WEAKER);
    }
    g->rf_limit_samples = 0u;

    if (g->state == ARC_V4_GLIDE_RF_LIMIT)
        g->state = ARC_V4_GLIDE_HOLD;

    if (g->state == ARC_V4_GLIDE_VERIFY) {
        ++g->verify_samples;

        /* A small step that restored broad margin stops immediately. */
        if (comfortable_corridor(g)) {
            g->state = ARC_V4_GLIDE_HOLD;
            g->direction = ARC_V4_GLIDE_DIR_NONE;
            g->last_action = ARC_V4_GLIDE_ACTION_NONE;
            g->trend_samples = 0u;
            baseline_follow_hold(g);
            return g->gain;
        }

        /* If fresh post-write Q4 is still genuinely near the weak/strong edge,
         * continue the glide. This is closed-loop slew, not a precomputed
         * anchor march. */
        if (g->verify_samples >= GLIDE_VERIFY_SAMPLES) {
            if (weak_edge_absolute(g) && g->gain < g->table.max_index)
                return glide_step(g, ARC_V4_GLIDE_WEAKER);
            if (strong_edge_absolute(g))
                return glide_step(g, ARC_V4_GLIDE_STRONGER);

            g->state = ARC_V4_GLIDE_HOLD;
            g->direction = ARC_V4_GLIDE_DIR_NONE;
            g->last_action = ARC_V4_GLIDE_ACTION_NONE;
            g->trend_samples = 0u;
            baseline_follow_hold(g);
        }
        return g->gain;
    }

    /* Edge-triggered normal motion: require at least two independent metrics
     * to move away from the HOLD baseline. A stable P9/Q75, for example, is
     * allowed to remain at the current gain indefinitely. */
    bool weak_trend =
        g->weak_votes >= 2 &&
        (g->fast_p <= 14 || g->fast_q <= 93 || g->fast_origin >= 120);

    bool strong_trend =
        g->strong_votes >= 2 &&
        (g->fast_p >= 30 || g->fast_clip >= 8);

    if (weak_trend) {
        if (g->direction != ARC_V4_GLIDE_WEAKER) {
            g->direction = ARC_V4_GLIDE_WEAKER;
            g->trend_samples = 1u;
        } else if (g->trend_samples < 255u) {
            ++g->trend_samples;
        }

        g->state = ARC_V4_GLIDE_UP;
        if (g->trend_samples >= GLIDE_WEAK_CONFIRM_SAMPLES) {
            if (g->gain >= g->table.max_index) return g->gain;
            return glide_step(g, ARC_V4_GLIDE_WEAKER);
        }
        return g->gain;
    }

    if (strong_trend) {
        if (g->direction != ARC_V4_GLIDE_STRONGER) {
            g->direction = ARC_V4_GLIDE_STRONGER;
            g->trend_samples = 1u;
        } else if (g->trend_samples < 255u) {
            ++g->trend_samples;
        }

        g->state = ARC_V4_GLIDE_DOWN;
        if (g->trend_samples >= GLIDE_STRONG_CONFIRM_SAMPLES)
            return glide_step(g, ARC_V4_GLIDE_STRONGER);
        return g->gain;
    }

    /* No edge: abandon an unconfirmed glide and slowly move the reference with
     * the real steady state. This is the main anti-hunting invariant. */
    g->state = ARC_V4_GLIDE_HOLD;
    g->direction = ARC_V4_GLIDE_DIR_NONE;
    g->last_action = ARC_V4_GLIDE_ACTION_NONE;
    g->trend_samples = 0u;
    baseline_follow_hold(g);

    /* HOLD means exactly zero writes, even if the absolute P/Q value is lower
     * than ideal but remains stable. */
    (void)healthy_corridor(g);
    return g->gain;
}

const char *arc_v4_glide_state_name(arc_v4_glide_state_t state)
{
    switch (state) {
    case ARC_V4_GLIDE_UP:       return "GLIDE-UP";
    case ARC_V4_GLIDE_DOWN:     return "GLIDE-DOWN";
    case ARC_V4_GLIDE_VERIFY:   return "VERIFY";
    case ARC_V4_GLIDE_RF_LIMIT: return "RF_LIMIT";
    default:                    return "HOLD";
    }
}

const char *arc_v4_glide_direction_name(arc_v4_glide_direction_t direction)
{
    switch (direction) {
    case ARC_V4_GLIDE_WEAKER:   return "WEAKER";
    case ARC_V4_GLIDE_STRONGER: return "STRONGER";
    default:                    return "NONE";
    }
}

const char *arc_v4_glide_action_name(arc_v4_glide_action_t action)
{
    switch (action) {
    case ARC_V4_GLIDE_ACTION_STEP:   return "STEP";
    case ARC_V4_GLIDE_ACTION_ESCAPE: return "ESCAPE";
    default:                         return "NONE";
    }
}
