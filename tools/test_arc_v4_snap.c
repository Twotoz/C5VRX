#include <assert.h>
#include <stdio.h>

#include "arc_v4_snap.h"

static arc_gain_table_t table(void)
{
    arc_gain_table_t t = {0};
    t.max_index = 81u;
    return t;
}

static arc_v4_snap_observation_t obs(int p, int q, int clip, int origin, int winding)
{
    return (arc_v4_snap_observation_t) {
        .p_median = p,
        .q_phase = q,
        .clip_permille = clip,
        .origin_permille = origin,
        .winding_permille = winding,
    };
}

static void feed(arc_v4_snap_t *s, arc_v4_snap_observation_t o, unsigned n)
{
    for (unsigned i = 0; i < n; ++i)
        (void)arc_v4_snap_tick(s, &o);
}

static void settle_lock(arc_v4_snap_t *s, arc_v4_snap_observation_t good)
{
    feed(s, good, 12u);
    assert(s->state == ARC_V4_SNAP_LOCK);
}

int main(void)
{
    arc_gain_table_t t = table();
    arc_v4_snap_t s;
    const arc_v4_snap_observation_t good =
        obs(17, 99, 0, 20, 20);

    /* Stable Q4 must stay a true zero-write LOCK indefinitely. */
    arc_v4_snap_reset(&s, &t, 54u);
    settle_lock(&s, good);
    uint32_t handoffs = s.handoffs;
    feed(&s, good, 500u);
    assert(s.gain == 54u);
    assert(s.handoffs == handoffs);
    assert(s.state == ARC_V4_SNAP_LOCK);

    /* A one-sample deep fade is not enough to force a physical handoff. */
    arc_v4_snap_observation_t one_fade = obs(2, 5, 0, 900, 20);
    (void)arc_v4_snap_tick(&s, &one_fade);
    assert(s.gain == 54u);
    feed(&s, good, 8u);
    assert(s.gain == 54u);
    assert(s.state == ARC_V4_SNAP_LOCK);

    /* A sustained pre-cliff weak margin uses the measured anchor ladder.
     * SOFT confirmation is ~300 ms at the 6 ms observer cadence. */
    arc_v4_snap_observation_t precliff = obs(9, 74, 0, 260, 80);
    feed(&s, precliff, 55u);
    assert(s.gain == 70u);
    assert(s.state == ARC_V4_SNAP_VERIFY);
    assert(s.handoffs == handoffs + 1u);

    /* Post-write samples are epoch-local: discard first, then good fresh Q4
     * relocks in a few samples rather than sleeping for 500 ms. */
    feed(&s, good, 10u);
    assert(s.gain == 70u);
    assert(s.state == ARC_V4_SNAP_LOCK);

    /* FAST pre-cliff deterioration should hand off in roughly 100 ms. */
    arc_v4_snap_observation_t fast_weak = obs(7, 50, 0, 470, 120);
    feed(&s, fast_weak, 20u);
    assert(s.gain == 78u);

    feed(&s, good, 10u);
    assert(s.state == ARC_V4_SNAP_LOCK);

    /* A real quantizer-collapse signature skips multiple anchors. */
    arc_v4_snap_reset(&s, &t, 54u);
    settle_lock(&s, good);
    arc_v4_snap_observation_t cliff = obs(3, 10, 0, 850, 100);
    feed(&s, cliff, 3u);
    assert(s.gain == 81u);
    assert(s.state == ARC_V4_SNAP_VERIFY);

    /* Severe overload moves down aggressively using the same calibrated
     * ladder. From G81 it should land around G70, not walk down by -1. */
    arc_v4_snap_reset(&s, &t, 81u);
    settle_lock(&s, good);
    arc_v4_snap_observation_t overload = obs(65, 99, 160, 0, 20);
    feed(&s, overload, 3u);
    assert(s.gain == 70u);

    /* There is deliberately no fixed one-second reversal guard. Once fresh
     * post-write IQ proves the opposite condition, SNAP can reverse quickly. */
    feed(&s, cliff, 12u);
    assert(s.gain > 70u);

    /* At G81, ordinary marginal but still coherent video must not be called an
     * RF limit. This matches the 25 mW walk where Q often remained 60-100%. */
    arc_v4_snap_reset(&s, &t, 81u);
    settle_lock(&s, good);
    arc_v4_snap_observation_t marginal = obs(8, 62, 0, 300, 100);
    feed(&s, marginal, 80u);
    assert(s.gain == 81u);
    assert(s.state != ARC_V4_SNAP_RF_LIMIT);

    /* Only persistent critical starvation at the vendor ceiling becomes
     * explicit RF_LIMIT. */
    feed(&s, cliff, 80u);
    assert(s.gain == 81u);
    assert(s.state == ARC_V4_SNAP_RF_LIMIT);

    puts("arc_v4_snap calibrated handoff tests passed");
    return 0;
}
