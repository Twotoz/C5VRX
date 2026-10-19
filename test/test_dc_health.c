/* C5VRX-5 by Twotoz and the C5VRX contributors: host test of the in-flight
 * DC health watch and the next-boot repair flags (main/dc_health.h).
 *
 *   cc -O2 -std=c11 -I../main test_dc_health.c -o t && ./t
 */
#include <stdio.h>
#include "dc_health.h"

static unsigned s_fail, s_run;
static void check(const char *name, int ok)
{
    ++s_run;
    printf("%-4s %s\n", ok ? "ok" : "FAIL", name);
    s_fail += !ok;
}

#define S 1000000LL
#define NORMAL 0x8a00u
#define NARROW 0x0a03u

/* Feed one observation per 250 ms tick from t0 for `seconds`; count latches. */
static unsigned run(dc_watch_t *w, unsigned reason, uint16_t freq, uint16_t key, bool qualified, bool bad,
                    int64_t t0, int64_t seconds)
{
    unsigned latches = 0;
    for (int64_t t = t0; t < t0 + seconds * S; t += S / 4) latches += dc_watch_step(w, reason, freq, key, qualified, bad, t);
    return latches;
}

int main(void)
{
    dc_watch_t w = {0};
    /* Watch: sustain, hysteresis, unknown evidence. */
    check("table: 19 s bad does not latch", run(&w, DC_HEALTH_TABLE, 5865, NORMAL, true, true, 1 * S, 19) == 0);
    check("table: latches once after 20 s", run(&w, DC_HEALTH_TABLE, 5865, NORMAL, true, true, 20 * S, 30) == 1);
    check("latched stays latched (no repeat, no NVS spam)", w.latched &&
          run(&w, DC_HEALTH_TABLE, 5865, NORMAL, true, true, 50 * S, 60) == 0);

    w = (dc_watch_t){0};
    (void)run(&w, DC_HEALTH_DRIFT, 5865, NORMAL, true, true, 1 * S, 40);
    (void)run(&w, DC_HEALTH_DRIFT, 5865, NORMAL, true, false, 41 * S, 1);   /* one good window: a fade, a trim */
    check("drift: a good window restarts the 60 s", run(&w, DC_HEALTH_DRIFT, 5865, NORMAL, true, true, 42 * S, 59) == 0);

    w = (dc_watch_t){0};
    (void)run(&w, DC_HEALTH_DRIFT, 5865, NORMAL, true, true, 1 * S, 30);
    (void)run(&w, DC_HEALTH_DRIFT, 5865, NORMAL, false, false, 31 * S, 600);   /* menu, scan, no estimate */
    check("unqualified windows neither clear nor latch", !w.latched && w.since_us != 0);
    check("drift then latches after 60 s of qualified bad evidence",
          run(&w, DC_HEALTH_DRIFT, 5865, NORMAL, true, true, 631 * S, 2) == 1);

    w = (dc_watch_t){0};
    (void)run(&w, DC_HEALTH_TABLE, 5865, NORMAL, true, true, 1 * S, 15);
    check("a channel change starts over", run(&w, DC_HEALTH_TABLE, 5732, NORMAL, true, true, 16 * S, 15) == 0 &&
          w.freq == 5732);
    check("a profile (filter) change starts over", run(&w, DC_HEALTH_TABLE, 5732, NARROW, true, true, 31 * S, 15) == 0);
    check("never qualified: never latches", run(&w, DC_HEALTH_FILTER, 5865, NORMAL, false, true, 1 * S, 3600) == 0);
    check("filter: 10 s sustain", run(&w, DC_HEALTH_FILTER, 5865, NORMAL, true, true, 1 * S, 11) == 1);
    w = (dc_watch_t){0};
    check("time zero is a valid start", !dc_watch_step(&w, DC_HEALTH_TABLE, 5865, NORMAL, true, true, 0) &&
          w.since_us != 0);

    /* Flags: one per (frequency, filter), persistent, bounded. */
    dc_flags_t x;
    dc_flags_init(&x);
    check("init valid and empty", dc_flags_valid(&x) && x.count == 0 && dc_flags_find(&x, 5865, NORMAL) < 0);
    check("add: changed (save)", dc_flags_add(&x, 5865, NORMAL, DC_HEALTH_DRIFT));
    check("same flag again: unchanged (no write)", !dc_flags_add(&x, 5865, NORMAL, DC_HEALTH_DRIFT) && x.count == 1);
    check("new reason: updated in place", dc_flags_add(&x, 5865, NORMAL, DC_HEALTH_TABLE) && x.count == 1 &&
          x.f[0].reason == DC_HEALTH_TABLE);
    check("other filter, same channel: own flag", dc_flags_add(&x, 5865, NARROW, DC_HEALTH_TABLE) && x.count == 2);
    check("clear: only that filter", dc_flags_clear(&x, 5865, NORMAL) && dc_flags_find(&x, 5865, NORMAL) < 0 &&
          dc_flags_find(&x, 5865, NARROW) == 0);
    check("clear again: nothing (no write)", !dc_flags_clear(&x, 5865, NORMAL));
    for (unsigned k = 0; k < DC_FLAGS + 2u; ++k) (void)dc_flags_add(&x, (uint16_t)(5600 + k), NORMAL, DC_HEALTH_TABLE);
    check("full: bounded, the oldest goes", x.count == DC_FLAGS && dc_flags_find(&x, 5865, NARROW) < 0 &&
          dc_flags_find(&x, 5600 + DC_FLAGS + 1u, NORMAL) == (int)DC_FLAGS - 1);
    dc_flags_t bad;
    memset(&bad, 0xff, sizeof(bad));
    check("garbage blob rejected", !dc_flags_valid(&bad));
    check("reason names", !strcmp(dc_health_reason_name(DC_HEALTH_DRIFT), "drift") &&
          !strcmp(dc_health_reason_name(99), "unknown"));

    printf("%u/%u dc health checks passed\n", s_run - s_fail, s_run);
    return s_fail != 0;
}
