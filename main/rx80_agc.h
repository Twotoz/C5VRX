/* C5VRX by Twotoz/contributors: experimental RX80 headroom controller.
 * Uses Direct Gain's existing Q4/I4 observations and calibrated row steps.
 * It acts on fresh IQ40 observer views, never paces the sample pipeline.
 * Bounds and delays are lab defaults, not measured range optimization. */
#pragma once
#include "gain.h"

typedef struct {
    uint64_t last_us, changed_us, low_us, attack_us, request_us;
    uint32_t attacks, releases, holds;
    uint8_t row;
    bool ready, low;
    /* Refreshed by the owner before each tick, not inferred from a DC point. */
    uint8_t dc_floor;
    bool carrier_recent;
} rx80_agc_t;

static inline uint8_t rx80_agc_step(const int16_t *db, uint8_t current, uint8_t max, int delta)
{
    const int target=db[current]+delta;
    if (delta<0) {
        for (int k=(int)current-1;k>=2;--k) if (db[k]<=target) return (uint8_t)k;
        return 2;
    }
    for (unsigned k=(unsigned)current+1;k<=max;++k) if (db[k]>=target) return (uint8_t)k;
    return max;
}

static inline uint8_t rx80_agc_tick(rx80_agc_t *a,const dg_observation_t *o,
                                   const int16_t *db,uint8_t current,uint8_t max)
{
    if (!o || !db || max<2 || current<2 || current>max) return current;
    const uint64_t now=o->observed_us;
    if (a->ready && now<=a->last_us) return current;
    a->last_us=now;
    if (!a->ready || a->row!=current) {
        a->ready=true;a->row=current;a->changed_us=now;a->low=false;
    }
    /* Ignore gain/PHY settling, and bound retries if a proposal is refused. */
    if (now-a->changed_us<1500u || (a->request_us && now-a->request_us<2000u)) return current;
    /* Preserve C5VRX's DC ownership. A static DC point or a one-sided rail
     * is not evidence of an oversized RF circle. Dropping gain there sends
     * the controller back into the same uncorrected floor row repeatedly. */
    const bool dc_point=(dg_one_sided(o) && o->clip_pm>=10u) || (dg_stuck(o) && o->p95>=25u);
    const bool floor_release=dc_point && !a->carrier_recent && current<a->dc_floor;
    if (dc_point && !floor_release) {
        a->low=false;++a->holds;return current;
    }
    /* P95=45 is a radius of only ~6.7 cells, not clipping. Real board
     * traces repeatedly reached it with clip=0/rails=0; attacking there
     * created the gain changes. Reserve this power-only guard for the
     * quantizer edge (~8 cells), retaining the independent rail guard. */
    if (!floor_release && ((!dg_one_sided(o) && o->clip_pm>=10u) || o->p95>=65u)) {
        a->low=false;a->attack_us=now;
        uint8_t next=rx80_agc_step(db,current,max,o->clip_pm>=100u ? -6*256 : -3*256);
        if (next!=current) { ++a->attacks;a->request_us=now; }
        return next;
    }
    /* Require a sustained low envelope. A few low-frequency video probes
     * can be DC-dominated; do not chase each one upward and then fold the
     * following sync/burst. Healthy probes reset this release timer. */
    if (!floor_release && (o->p50>=9u || o->p95>=25u)) {
        a->low=false;++a->holds;return current;
    }
    if (!a->low) { a->low=true;a->low_us=now; }
    if (now-a->low_us<60000u || (a->attack_us && now-a->attack_us<200000u)) return current;
    /* The original p95 hold band was only ~2.5 dB wide. A 3-dB release can
     * jump straight through it and make a deterministic attack/release
     * cycle; climb in calibrated 1-dB steps instead. */
    uint8_t next=rx80_agc_step(db,current,max,256);
    if (floor_release && next>a->dc_floor) next=a->dc_floor;
    if (next!=current) { ++a->releases;a->request_us=now;a->low=false; }
    return next;
}
