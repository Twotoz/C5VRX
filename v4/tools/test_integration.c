/* C5VRX by Twotoz and contributors: integrated range policy regressions. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "rf.h"
#include "direct_gain_v3.h"
#include "c5vrx4.h"
unsigned char phy_param[0x800];
static uint8_t fixed = C5VRX4_LANE_ADAPTIVE;
static bool reference_mode;
static rf_iq_lane_stats_t lane_stats;
uint8_t c5vrx4_fixed_lane(void) { return fixed; }
bool c5vrx4_history_enabled(void) { return false; }
bool c5vrx4_staged_gain_recovery(void) { return reference_mode; }
uint8_t rf_get_iq_lanes(void) { return lane_stats.last_to; }
void rf_get_iq_lane_stats(rf_iq_lane_stats_t *s) { *s = lane_stats; }
#include "../lanes.c"

/* Saturation counts once it has lasted DG3_BURST_US (Wi-Fi burst gate):
 * replay the same window 1 ms earlier, then return the real decision. */
static uint8_t sat(direct_gain_v3_t *v, dg3_observation_t o)
{
    dg3_observation_t first = o;
    first.observed_us = o.observed_us - 1000u;
    (void)direct_gain_v3_tick(v, &first);
    return direct_gain_v3_tick(v, &o);
}


/* zerowidth/C5VRX PR #3: Wi-Fi stations put 0.1-0.5 ms bursts on the channel
 * 1-4% of the time and each clips every sample while it lasts. Reading them
 * as overload held a weak carrier 20+ gain steps low. FM survives clipping,
 * so isolated bursts must not cost the edge its gain; persistent saturation
 * (a strong carrier) must still come down. */
static void test_wifi_burst(void)
{
    arc_gain_table_t table;
    arc_gain_table_from_bytes(&table, NULL, 83);
    direct_gain_v3_t v = {0};
    reference_mode = true; fixed = 2u;
    direct_gain_v3_reset(&v, &table, table.max_index, 62);
    direct_gain_v3_enable_lanes(&v, 2);
    const uint8_t top = table.max_index;
    dg3_observation_t weak = {.p50=9, .p90=16, .p95=20, .origin_pm=150, .clip_pm=0,
                              .coherence=70, .observed_us=1000000};
    dg3_observation_t burst = {.p50=113, .p90=113, .p95=113, .origin_pm=0, .clip_pm=1000,
                               .coherence=10};
    uint8_t lowest = top;
    for (unsigned cycle = 0; cycle < 20; ++cycle) {
        for (unsigned n = 0; n < 60; ++n) {        /* 12 ms of weak carrier */
            weak.observed_us += 200;
            uint8_t g = direct_gain_v3_tick(&v, &weak);
            if (v.state == DG3_SETTLE && v.write_us == weak.observed_us)
                direct_gain_v3_sync_applied(&v, g, weak.observed_us);
            if (g < lowest) lowest = g;
        }
        burst.observed_us = weak.observed_us;
        for (unsigned n = 0; n < 1 + cycle % 5; ++n) { /* 0.1..0.5 ms burst */
            burst.observed_us += 100;
            uint8_t g = direct_gain_v3_tick(&v, &burst);
            if (v.state == DG3_SETTLE && v.write_us == burst.observed_us)
                direct_gain_v3_sync_applied(&v, g, burst.observed_us);
            if (g < lowest) lowest = g;
        }
        weak.observed_us = burst.observed_us;
    }
    fprintf(stderr, "wifi_burst: lowest gain %u of %u, writes %u, overloads %u\n",
           (unsigned)lowest, (unsigned)top, (unsigned)v.writes, (unsigned)v.overloads);
    assert(lowest == top && v.writes == 0u);
    /* A carrier that keeps clipping for 3 ms is real overload: come down. */
    for (unsigned n = 0; n < 30; ++n) {
        burst.observed_us += 100;
        uint8_t g = direct_gain_v3_tick(&v, &burst);
        if (v.state == DG3_SETTLE && v.write_us == burst.observed_us)
            direct_gain_v3_sync_applied(&v, g, burst.observed_us);
        if (g < lowest) lowest = g;
    }
    assert(lowest < top && v.overloads > 0u);
    reference_mode = false; fixed = C5VRX4_LANE_ADAPTIVE;
}

static void test_post_drop_no_carrier(void)
{
    arc_gain_table_t table;
    arc_gain_table_from_bytes(&table, NULL, 83);
    direct_gain_v3_t v = {0};
    fixed = C5VRX4_LANES_FINE;
    direct_gain_v3_reset(&v, &table, 66, 62);
    direct_gain_v3_enable_lanes(&v, 2);
    dg3_observation_t rail = {.p50=113, .p95=113, .clip_pm=800,
                              .coherence=60, .observed_us=99000};
    /* Saturation counts after DG3_BURST_US (Wi-Fi burst gate). */
    assert(direct_gain_v3_tick(&v, &rail) == 66);
    rail.observed_us = 100000;
    assert(direct_gain_v3_tick(&v, &rail) == 20);
    direct_gain_v3_sync_applied(&v, 20, rail.observed_us);
    uint32_t writes = v.writes;
    dg3_observation_t quiet = {.p50=1, .p95=3, .origin_pm=980,
                               .coherence=0, .observed_us=100200};
    /* Transient near-origin IQ after the RF-stage drop must not cancel
     * settling by requesting maximum gain. */
    assert(direct_gain_v3_tick(&v, &quiet) == 20);
    assert(v.state == DG3_SETTLE && v.writes == writes);
    quiet.observed_us = 100299;
    assert(direct_gain_v3_tick(&v, &quiet) == 20);
    /* A carrier returns once settled: retain G20, with no excursion to G83. */
    dg3_observation_t good = {.p50=22, .p95=40, .coherence=99,
                              .observed_us=100600};
    assert(direct_gain_v3_tick(&v, &good) == 20);
    assert(v.state == DG3_HOLD && v.writes == writes);
    /* Real loss still listens at maximum after the guard. */
    quiet.observed_us = 101000;
    assert(direct_gain_v3_tick(&v, &quiet) == 83);

    /* Previously measured longer settling must also protect recovery. */
    direct_gain_v3_reset(&v, &table, 66, 62);
    v.settle_us[DG3_RF] = 1000;
    rail.observed_us = 200000;
    assert(sat(&v, rail) == 20);
    direct_gain_v3_sync_applied(&v, 20, rail.observed_us);
    quiet.observed_us = 200600;
    assert(direct_gain_v3_tick(&v, &quiet) == 20);
    quiet.observed_us = 200750;
    assert(direct_gain_v3_tick(&v, &quiet) == 83);
    /* Overload on that upward write keeps its safety response once it has
     * clipped for 1 ms (FM survives that; a Wi-Fi burst does not last). */
    rail.observed_us = 201800;
    assert(sat(&v, rail) == 20);
    fixed = C5VRX4_LANE_ADAPTIVE;
}

static void test_benchmark_lane_escape(void)
{
    /* Same starting gain and fine-lane overload, contrasting the two lane
     * policies. This tests control decisions, not RF/video quality. */
    arc_gain_table_t table;
    arc_gain_table_from_bytes(&table, NULL, 83);
    direct_gain_v3_t v = {0};
    dg3_observation_t rail = {.p50=113, .p95=113, .clip_pm=800,
                              .coherence=60, .observed_us=300000};
    fixed = 1u;
    direct_gain_v3_reset(&v, &table, 66, 62);
    direct_gain_v3_enable_lanes(&v, 2);
    assert(v.lane == 1u);
    assert(sat(&v, rail) == 20);
    assert(v.lane == 1u && v.writes == 1u);

    fixed = C5VRX4_LANE_ADAPTIVE;
    memset(&v, 0, sizeof(v));
    direct_gain_v3_reset(&v, &table, 66, 62);
    direct_gain_v3_enable_lanes(&v, 2);
    v.lane = 1u; /* Already receiving on fine when overload arrives. */
    assert(sat(&v, rail) == 66);
    assert(v.lane == 0u && v.writes == 0u && v.fold_drops == 1u);
    /* Pre-switch rail evidence must not trigger a gain collapse. */
    rail.observed_us += 200;
    assert(direct_gain_v3_tick(&v, &rail) == 66 && v.writes == 0u);
    dg3_observation_t good = {.p50=22, .p95=40, .coherence=99,
                              .observed_us=300600};
    assert(direct_gain_v3_tick(&v, &good) == 66 && v.writes == 0u);
    /* Genuine overload persisting on coarse still uses the safety floor. */
    rail.observed_us = 301500;
    assert(sat(&v, rail) == 20 && v.writes == 1u);
}

static void test_reference_overload_recovery(void)
{
    arc_gain_table_t table;
    arc_gain_table_from_bytes(&table, NULL, 83);
    direct_gain_v3_t v = {0};
    reference_mode = true;
    fixed = C5VRX4_LANE_ADAPTIVE;
    direct_gain_v3_reset(&v, &table, 83, 62);
    dg3_observation_t rail = {.p50=113, .p95=113, .clip_pm=800,
                              .coherence=60, .observed_us=100000};
    uint8_t lower = sat(&v, rail);
    assert(lower > 20 && lower < 83); /* BB first, not survival floor. */
    direct_gain_v3_sync_applied(&v, lower, rail.observed_us);
    dg3_observation_t quiet = {.p50=1, .p95=3, .origin_pm=980,
                               .coherence=0, .observed_us=100200};
    assert(direct_gain_v3_tick(&v, &quiet) == lower); /* stale quiet */
    quiet.observed_us = 101000;
    uint8_t higher = direct_gain_v3_tick(&v, &quiet);
    assert(higher > lower && higher < 83); /* recovery step, not max */
    direct_gain_v3_sync_applied(&v, higher, quiet.observed_us);
    rail.observed_us = 102100;
    assert(sat(&v, rail) < higher); /* upward overload safety, after 1 ms */

    /* Settled gain-dependent plant: rail at >=66, starved below34, clean
     * between. The old hard-floor/max loop cannot find its healthy band. */
    memset(&v, 0, sizeof(v));
    direct_gain_v3_reset(&v, &table, 83, 62);
    uint64_t now = 200000;
    for (unsigned n = 0; n < 100; ++n, now += 1000) {
        dg3_observation_t o = v.current_gain >= 66 ? rail :
                             v.current_gain < 34 ? quiet :
                             (dg3_observation_t){.p50=22, .p95=40, .coherence=99};
        o.observed_us = now;
        uint8_t before = v.current_gain;
        uint8_t target = direct_gain_v3_tick(&v, &o);
        if (target != before) direct_gain_v3_sync_applied(&v, target, now);
    }
    assert(v.current_gain >= 34 && v.current_gain < 66 && v.state == DG3_HOLD);
    assert(v.writes < 10 && v.verified > 0);

    /* Persistent overload can still reach the floor, then true signal loss
     * must recover all the way to table maximum; no low-gain trap. */
    for (unsigned n = 0; n < 100 && v.current_gain != 20; ++n, now += 1000) {
        rail.observed_us = now;
        uint8_t before = v.current_gain;
        uint8_t target = direct_gain_v3_tick(&v, &rail);
        if (target != before) direct_gain_v3_sync_applied(&v, target, now);
    }
    assert(v.current_gain == 20);
    for (unsigned n = 0; n < 100 && v.current_gain != 83; ++n, now += 1000) {
        quiet.observed_us = now;
        uint8_t before = v.current_gain;
        uint8_t target = direct_gain_v3_tick(&v, &quiet);
        if (target != before) direct_gain_v3_sync_applied(&v, target, now);
    }
    assert(v.current_gain == 83);
    reference_mode = false;
}

int main(void)
{
    test_wifi_burst();
    test_post_drop_no_carrier();
    test_benchmark_lane_escape();
    test_reference_overload_recovery();
    uint8_t fit[256], outside[256];
    memset(fit, 0x11, sizeof(fit)); memset(outside, 0x33, sizeof(outside));
    assert(c5vrx4_lane_target(0, 2, fit, sizeof(fit), 1000) == 0);
    assert(c5vrx4_lane_target(0, 2, outside, sizeof(outside), 1200) == 0);
    assert(c5vrx4_lane_target(0, 2, fit, sizeof(fit), 1400) == 0);
    assert(c5vrx4_lane_target(0, 2, fit, sizeof(fit), 1600) == 1);
    assert(c5vrx4_lane_target(2, 0, NULL, 0, 1800) == 0);
    lane_stats.switches = 1; lane_stats.last_switch_us = 2000;
    assert(!c5vrx4_lane_window_ready(1999));
    assert(!c5vrx4_lane_window_ready(2209));
    assert(c5vrx4_lane_window_ready(2210));

    arc_gain_table_t table; arc_gain_table_from_bytes(&table, NULL, 81);
    direct_gain_v3_t v;
    dg3_observation_t severe = {.p50=65, .p95=113, .clip_pm=738,
                               .coherence=0, .observed_us=10000};
    direct_gain_v3_reset(&v, &table, 81, 62);
    direct_gain_v3_enable_lanes(&v, 2);
    assert(sat(&v, severe) == 20 && v.writes == 1);
    severe.observed_us += 100; /* Old saturated IQ after the drop: no hunt. */
    assert(direct_gain_v3_tick(&v, &severe) == 20 && v.writes == 1);
    direct_gain_v3_reset(&v, &table, 81, 62);
    direct_gain_v3_enable_lanes(&v, 2); v.lane = 2;
    severe.observed_us = 20000;
    assert(sat(&v, severe) == 81 && v.lane == 0);
    severe.observed_us += 1000;
    assert(direct_gain_v3_tick(&v, &severe) == 20);
    direct_gain_v3_reset(&v, &table, 81, 62);
    direct_gain_v3_enable_lanes(&v, 2);
    severe.clip_pm = 200; severe.observed_us = 30000;
    assert(sat(&v, severe) > 20); /* Moderate: physical stage cut. */
    direct_gain_v3_reset(&v, &table, 35, 62);
    dg3_observation_t good = {.p50=22, .p95=40, .coherence=99};
    for (unsigned n=0;n<100;n++) {
        good.observed_us = 40000 + n*1000;
        assert(direct_gain_v3_tick(&v, &good) == 35);
    }
    assert(v.writes == 0);

    /* Default fixed fine {9,7,6,5}: the lane never changes, not to listen on
     * ultrafine without a carrier, not to escape rail codes, not when the
     * envelope is starved or high; analog gain does all the tracking. */
    fixed = 1u;
    /* A moving indoor channel must not permanently remove a whole gain bank.
     * Reproduce three settled low-coherence visits, then the old saved bank
     * blacklist: a weak carrier must still reach the remaining gain range. */
    memset(&v, 0, sizeof(v));
    direct_gain_v3_reset(&v, &table, 63, 62);
    dg3_observation_t fade = {.p50=9, .p90=13, .p95=17, .origin_pm=400,
                              .coherence=20, .observed_us=90000};
    for (unsigned n = 0; n < 3; ++n) {
        direct_gain_v3_sync_applied(&v, 63, fade.observed_us);
        fade.observed_us += 1000;
        (void)direct_gain_v3_tick(&v, &fade);
    }
    assert(v.bad_state[63] == 0u && v.learned == 0u);
    memset(&v, 0, sizeof(v));
    direct_gain_v3_reset(&v, &table, 62, 62);
    for (unsigned g = 63; g <= 73; ++g) v.bad_state[g] = 3u;
    fade.origin_pm = 100; fade.coherence = 90;
    for (unsigned n = 0; n < 2000; ++n) {
        fade.observed_us += 200;
        uint8_t gain = direct_gain_v3_tick(&v, &fade);
        if (v.state == DG3_SETTLE && v.write_us == fade.observed_us)
            direct_gain_v3_sync_applied(&v, gain, fade.observed_us);
    }
    assert(v.current_gain == table.max_index && v.writes > 0u);
    direct_gain_v3_reset(&v, &table, 81, 62);
    direct_gain_v3_enable_lanes(&v, 2);
    assert(v.lane == 1u && v.lane_cap == 1u);
    dg3_observation_t none = {.p50=1, .p95=4, .origin_pm=900, .coherence=5,
                              .observed_us=100000};
    assert(direct_gain_v3_tick(&v, &none) == 81 && v.lane == 1u);
    dg3_observation_t starved = {.p50=3, .p95=12, .origin_pm=200, .coherence=80,
                                 .observed_us=101000};
    for (unsigned n = 0; n < 20; ++n, starved.observed_us += 1000)
        assert(direct_gain_v3_tick(&v, &starved) == 81 && v.lane == 1u);
    dg3_observation_t rail = {.p50=30, .p95=80, .clip_pm=40, .coherence=90,
                              .observed_us=130000};
    for (unsigned n = 0; n < 20; ++n, rail.observed_us += 1000) {
        (void)direct_gain_v3_tick(&v, &rail);
        assert(v.lane == 1u);
    }
    assert(v.lane_changes == 0u && v.fold_drops == 0u);
    /* Severe overdrive or fold on the fixed lane takes the coarse-lane G20
     * floor instead of a lane escape, and refuses stale saturated IQ. */
    direct_gain_v3_reset(&v, &table, 81, 62);
    direct_gain_v3_enable_lanes(&v, 2);
    severe.observed_us = 200000; severe.clip_pm = 738;
    assert(sat(&v, severe) == 20 && v.lane == 1u);
    severe.observed_us += 100;
    assert(direct_gain_v3_tick(&v, &severe) == 20 && v.writes == 1);
    /* Fixed ultrafine keeps the earlier Z comparison behaviour. */
    fixed = 2u;
    direct_gain_v3_reset(&v, &table, 81, 62);
    direct_gain_v3_enable_lanes(&v, 2);
    assert(v.lane == 2u && v.lane_cap == 2u);
    /* A strong carrier folded by the small ultrafine window can show an
     * ordinary P50 (host model, rms10 cells: P50 15, P95 99, rails 174 pm).
     * Fixed lanes cannot escape, so the analog gain must come down. */
    direct_gain_v3_sync_applied(&v, 81, 300000);
    dg3_observation_t folded = {.p50=15, .p90=60, .p95=99, .origin_pm=69, .clip_pm=174,
                                .coherence=56, .observed_us=300100};
    uint8_t lowest = 81;
    for (unsigned n = 0; n < 40; ++n, folded.observed_us += 1000) {
        uint8_t gain = direct_gain_v3_tick(&v, &folded);
        if (v.state == DG3_SETTLE && v.write_us == folded.observed_us)
            direct_gain_v3_sync_applied(&v, gain, folded.observed_us);
        if (gain < lowest) lowest = gain;
    }
    assert(lowest < 81 && v.lane == 2u && v.lane_changes == 0u);
    puts("PASS: protected lanes, freshness, severe coarse overload floor, stale drop refusal, zero-write clean tracking and fixed fine/ultrafine lanes");
}
