/* C5VRX-5 by Twotoz and the C5VRX contributors: Direct Gain V6 carrier
 * memory (main/gain.c), actual dg_tick()/dg_reset_context().
 *
 * Board 2026-10-18: every AFC step and PHY lease rebinds the observer, and
 * V5's reset dropped the fade state, so a lost carrier jumped G21 -> max and
 * came back clipping (742 per mille) through a staged walk down. Checks:
 * the remembered gain survives a reset with a changed AFC offset but not a
 * channel change; a lost carrier climbs in bounded steps instead of jumping
 * to the maximum; saturation above the remembered gain is one write back to
 * it; a really-gone carrier still reaches the maximum; a fixed IQ point
 * (VTX off DC) never becomes the remembered gain.
 *
 *   cc -O2 -std=c11 -I../main test_gain_v6_memory.c ../main/gain.c ../main/arc_phy.c -lm -o t && ./t
 */
#include <stdio.h>
#include <string.h>
#include "gain.h"

unsigned char phy_param[2048];     /* arc_phy.c reference; unused for the 5 GHz table */

static unsigned s_fail, s_run;
#define CHECK(c, ...) do { ++s_run; if (!(c)) { ++s_fail; printf("FAIL %s:%d: ", __FILE__, __LINE__); \
                           printf(__VA_ARGS__); printf("\n"); } } while (0)

static dg_observation_t healthy(uint64_t t)
{
    return (dg_observation_t){.p50 = 20, .p90 = 30, .p95 = 34, .origin_pm = 20, .clip_pm = 0, .coherence = 90,
                              .repeat_pm = 50, .span = 10, .observed_us = t};
}
static dg_observation_t empty(uint64_t t)
{
    return (dg_observation_t){.p50 = 1, .p90 = 2, .p95 = 2, .origin_pm = 900, .coherence = 5,
                              .repeat_pm = 100, .span = 3, .observed_us = t};
}
static dg_observation_t clipped(uint64_t t)
{
    return (dg_observation_t){.p50 = 80, .p90 = 105, .p95 = 110, .origin_pm = 0, .clip_pm = 700, .coherence = 80,
                              .repeat_pm = 50, .span = 15, .observed_us = t};
}
static dg_observation_t stuck(uint64_t t)
{
    return (dg_observation_t){.p50 = 15, .p90 = 16, .p95 = 17, .origin_pm = 0, .clip_pm = 0, .coherence = 99,
                              .repeat_pm = 800, .span = 1, .observed_us = t};
}

/* The observer applies every target: model the hardware write and settle. */
static uint8_t step(direct_gain_t *g, dg_observation_t o)
{
    uint8_t before = g->current_gain, target = dg_tick(g, &o);
    if (target != before) dg_sync_applied(g, target, o.observed_us);
    return target;
}

static dg_map_context_t context(uint32_t freq, int32_t offset)
{
    return (dg_map_context_t){.freq_mhz = freq, .lane_mode = 2, .iq_scale = 1, .filter_skirt = 32,
                              .filter_bw20 = 1, .offset_khz = offset, .filter_code = 60};
}

int main(void)
{
    arc_gain_table_t table;
    arc_gain_table_from_bytes(&table, NULL, ARC_5G_GAIN_MAX);
    static direct_gain_t g;
    dg_map_context_t a = context(5865, 275);
    dg_reset_context(&g, &table, 21, &a);
    uint64_t t = 1000;
    for (int k = 0; k < 20; ++k, t += 200) step(&g, healthy(t));
    CHECK(g.anchor_valid && g.anchor_gain == 21, "healthy carrier remembered (valid=%u gain=%u)",
          g.anchor_valid, g.anchor_gain);

    /* AFC step: offset changes, receiver does not. */
    dg_map_context_t a2 = context(5865, 150);
    dg_reset_context(&g, &table, 21, &a2);
    CHECK(g.anchor_valid && g.anchor_gain == 21 && g.last_healthy_us, "memory survives an AFC-offset rebind");

    /* Lost carrier: hold, then bounded climb, never a jump to the maximum. */
    uint8_t peak = 21;
    unsigned writes_before = g.writes;
    for (int k = 0; k < 75; ++k, t += 200) {   /* 15 ms */
        uint8_t now = step(&g, empty(t));
        CHECK(now <= peak + 8u, "climb step bounded (G%u after G%u)", now, peak);
        if (now > peak) peak = now;
    }
    CHECK(peak > 21 && peak < table.max_index, "climbed but not to the maximum within 15 ms (peak G%u)", peak);
    CHECK(g.writes - writes_before <= 3u, "few climb writes in 15 ms (%u)", g.writes - writes_before);

    /* Carrier returns at its old level and clips: one write back. */
    writes_before = g.writes;
    uint8_t back = step(&g, clipped(t)); t += 200;
    CHECK(back == 21 && g.writes - writes_before == 1u, "one write back to the remembered gain (G%u, %u writes)",
          back, g.writes - writes_before);
    for (int k = 0; k < 10; ++k, t += 200) step(&g, healthy(t));
    CHECK(g.current_gain == 21, "stays at the remembered gain on the returned carrier (G%u)", g.current_gain);

    /* Really gone: still reaches the table maximum. */
    for (int k = 0; k < 500 && g.current_gain != table.max_index; ++k, t += 200) step(&g, empty(t));
    CHECK(g.current_gain == table.max_index, "VTX gone still reaches the maximum (G%u)", g.current_gain);

    /* Channel change: a different receiver context forgets the gain. */
    dg_map_context_t b = context(5845, 0);
    dg_reset_context(&g, &table, 40, &b);
    CHECK(!g.anchor_valid, "channel change forgets the remembered gain");

    /* VTX-off DC point reads healthy but is not a carrier. */
    for (int k = 0; k < 20; ++k, t += 200) step(&g, stuck(t));
    CHECK(!g.anchor_valid, "fixed IQ point never becomes the remembered gain");

    /* At or below the remembered gain, saturation keeps V5's burst gate. */
    dg_reset_context(&g, &table, 30, &b);
    for (int k = 0; k < 20; ++k, t += 200) step(&g, healthy(t));
    uint8_t burst = step(&g, clipped(t));
    CHECK(burst == 30, "a single clipped window at the remembered gain is a burst, not an overload (G%u)", burst);

    /* One-sided clipping (DC on a rail, gain.h dg_one_sided) is not overload:
     * no carrier at the table maximum, the noise cloud pushed onto Q-8 by
     * DC (board blackbox 2026-10-19: bursts of 255+ writes, 10-20 steps
     * down). Clipping on both rails of an axis still cuts the gain. */
    {
        static direct_gain_t h;
        dg_map_context_t c = context(5865, 0);
        dg_reset_context(&h, &table, table.max_index, &c);
        uint64_t u = 1000;
        for (int k = 0; k < 40; ++k, u += 200) step(&h, empty(u));
        const uint8_t top = h.current_gain;
        const uint32_t writes = h.writes;
        dg_observation_t dc = {.p50 = 6, .p90 = 30, .p95 = 40, .origin_pm = 300, .clip_pm = 160, .coherence = 30,
                               .repeat_pm = 200, .span = 9, .rails = 4u /* Q-8 only */};
        for (int k = 0; k < 200; ++k, u += 200) { dc.observed_us = u; step(&h, dc); }
        CHECK(h.current_gain == top && h.writes == writes && h.overloads == 0 && h.one_sided_clips == 200,
              "one-sided clipping leaves the gain (gain %u of %u, writes %lu, overloads %lu, one-sided %lu)",
              h.current_gain, top, (unsigned long)(h.writes - writes), (unsigned long)h.overloads,
              (unsigned long)h.one_sided_clips);
        dg_observation_t big = dc;
        big.rails = 4u | 8u;   /* Q-8 and Q+7: a carrier too large for the window */
        for (int k = 0; k < 40; ++k, u += 200) { big.observed_us = u; step(&h, big); }
        CHECK(h.current_gain < top && h.overloads > 0, "two-sided clipping still cuts the gain (gain %u, top %u)",
              h.current_gain, top);
        dg_observation_t legacy = dc;
        legacy.rails = 0;      /* no rail evidence: the old rule */
        CHECK(!dg_one_sided(&legacy) && dg_one_sided(&dc) && !dg_one_sided(&big), "rail classification");
    }
    printf("%s: %u checks, %u failures\n", s_fail ? "FAIL" : "PASS", s_run, s_fail);
    return s_fail ? 1 : 0;
}
