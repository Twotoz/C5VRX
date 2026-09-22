#include <assert.h>
#include <stdio.h>

#include "arc_v4_glide.h"

static arc_gain_table_t table(void)
{
    arc_gain_table_t t = {0};
    t.max_index = 81u;
    return t;
}

static arc_v4_glide_observation_t obs(int p, int q, int clip, int origin, int winding)
{
    return (arc_v4_glide_observation_t) {
        .p_median = p,
        .q_phase = q,
        .clip_permille = clip,
        .origin_permille = origin,
        .winding_permille = winding,
    };
}

static void feed(arc_v4_glide_t *g, arc_v4_glide_observation_t o, unsigned n)
{
    for (unsigned i = 0; i < n; ++i)
        (void)arc_v4_glide_tick(g, &o);
}

static void prime_good(arc_v4_glide_t *g)
{
    feed(g, obs(17, 99, 0, 20, 20), 16u);
    assert(g->state == ARC_V4_GLIDE_HOLD);
}

int main(void)
{
    arc_gain_table_t t = table();
    arc_v4_glide_t g;

    /* HOLD is a true zero-write state. */
    arc_v4_glide_reset(&g, &t, 54u);
    prime_good(&g);
    uint32_t writes = g.writes;
    feed(&g, obs(17, 99, 0, 20, 20), 500u);
    assert(g.gain == 54u);
    assert(g.writes == writes);
    assert(g.state == ARC_V4_GLIDE_HOLD);

    /* Stable low margin is NOT a reason to move. This is the key difference
     * from SNAP's level-triggered SOFT handoff. */
    arc_v4_glide_reset(&g, &t, 54u);
    feed(&g, obs(9, 75, 0, 260, 80), 300u);
    assert(g.gain == 54u);
    assert(g.writes == 0u);
    assert(g.state == ARC_V4_GLIDE_HOLD);

    /* A real weak-side trend starts early, before the old P~1/Q~0 cliff.
     * Normal movement is a small +2/+4 glide step, not an anchor jump. */
    arc_v4_glide_reset(&g, &t, 54u);
    prime_good(&g);
    arc_v4_glide_observation_t early_weak = obs(12, 88, 0, 170, 100);
    feed(&g, early_weak, 5u);
    assert(g.gain > 54u);
    assert(g.gain <= 58u);
    assert(g.last_action == ARC_V4_GLIDE_ACTION_STEP);

    /* Fresh post-write Q4 that is already comfortable stops the glide. */
    feed(&g, obs(15, 96, 0, 80, 40), 8u);
    assert(g.state == ARC_V4_GLIDE_HOLD);
    uint8_t held = g.gain;
    uint32_t held_writes = g.writes;
    feed(&g, obs(15, 96, 0, 80, 40), 100u);
    assert(g.gain == held);
    assert(g.writes == held_writes);

    /* If the first small step is still on the weak edge, GLIDE continues in
     * small closed-loop steps instead of leaping blindly to G70. */
    arc_v4_glide_reset(&g, &t, 54u);
    prime_good(&g);
    arc_v4_glide_observation_t edge = obs(8, 65, 0, 360, 120);
    feed(&g, edge, 6u);
    uint8_t first = g.gain;
    assert(first == 58u);
    feed(&g, edge, 8u);
    assert(g.gain > first);
    assert(g.gain <= 66u);
    assert(g.escape_writes == 0u);

    /* One bad fade must not start an ordinary glide. */
    arc_v4_glide_reset(&g, &t, 54u);
    prime_good(&g);
    arc_v4_glide_observation_t one_fade = obs(6, 45, 0, 500, 120);
    (void)arc_v4_glide_tick(&g, &one_fade);
    feed(&g, obs(17, 99, 0, 20, 20), 10u);
    assert(g.gain == 54u);
    assert(g.writes == 0u);

    /* Genuine starvation uses the calibrated ESCAPE path. */
    arc_v4_glide_reset(&g, &t, 54u);
    prime_good(&g);
    arc_v4_glide_observation_t starved = obs(3, 10, 0, 850, 100);
    (void)arc_v4_glide_tick(&g, &starved);
    assert(g.gain == 70u);
    assert(g.last_action == ARC_V4_GLIDE_ACTION_ESCAPE);
    assert(g.escape_writes == 1u);

    /* Extreme collapse may skip one extra measured operating region. */
    arc_v4_glide_reset(&g, &t, 54u);
    prime_good(&g);
    arc_v4_glide_observation_t collapse = obs(2, 5, 0, 950, 100);
    (void)arc_v4_glide_tick(&g, &collapse);
    assert(g.gain == 78u);

    /* Strong-side normal movement is slower and uses a small -2 step. */
    arc_v4_glide_reset(&g, &t, 70u);
    prime_good(&g);
    arc_v4_glide_observation_t stronger = obs(36, 99, 15, 10, 20);
    feed(&g, stronger, 12u);
    assert(g.gain == 68u);
    assert(g.last_action == ARC_V4_GLIDE_ACTION_STEP);

    /* Major overload escapes to the next calibrated lower region. */
    arc_v4_glide_reset(&g, &t, 70u);
    prime_good(&g);
    arc_v4_glide_observation_t overload = obs(65, 99, 120, 0, 20);
    (void)arc_v4_glide_tick(&g, &overload);
    assert(g.gain == 54u);
    assert(g.last_action == ARC_V4_GLIDE_ACTION_ESCAPE);

    /* No fixed one-second reversal guard: fresh evidence can reverse soon
     * after the post-write windows have been discarded. */
    feed(&g, obs(8, 62, 0, 430, 120), 12u);
    assert(g.gain > 54u);

    /* Stable marginal G81 must not become RF_LIMIT. */
    arc_v4_glide_reset(&g, &t, 81u);
    feed(&g, obs(8, 62, 0, 300, 100), 100u);
    assert(g.gain == 81u);
    assert(g.state != ARC_V4_GLIDE_RF_LIMIT);

    /* Persistent true collapse at the vendor ceiling is RF_LIMIT. */
    feed(&g, collapse, 20u);
    assert(g.gain == 81u);
    assert(g.state == ARC_V4_GLIDE_RF_LIMIT);

    puts("arc_v4_glide overlap/hysteresis tests passed");
    return 0;
}
