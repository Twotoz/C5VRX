/* C5VRX by Twotoz/contributors: camera-phase guard decisions. Host logic
 * only; no physical timing or monitor-lock claim. */
#include <assert.h>
#include <stdio.h>
#include "sync_phase_guard.h"

#define LINE 1271
static unsigned checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)

static uint32_t lcg(uint32_t *s) { *s = *s * 1103515245u + 12345u; return *s >> 8; }

int main(void)
{
    spg_t g = {0};
    spg_action_t a = {SPG_NONE, 0};
    /* Off-grid camera near the half-line wrap (+-635): coherent across it. */
    for (unsigned k = 0; k < SPG_LOCK; ++k) {
        CHECK(a.kind == SPG_NONE);
        a = spg_h(&g, (k & 1u) ? 634 : -634, LINE, 1000u + k);
    }
    CHECK(a.kind == SPG_JUMP_H && g.yield_h && spg_abs(a.value) >= 600);
    spg_reset(&g);
    CHECK(g.yield_h && !g.h_score);
    /* Re-anchored: aligned pulses end the yield, no further jump. */
    for (unsigned k = 0; k < SPG_LOCK; ++k) a = spg_h(&g, (int32_t)(k % 3u) - 1, LINE, 2000u + k);
    CHECK(a.kind == SPG_NONE && !g.yield_h && !spg_yield(&g, 2100u));

    /* Incoherent noise (many seeds): no yield, no jump, ever. */
    for (uint32_t seed = 1; seed < 200u; ++seed) {
        spg_t n = {0};
        uint32_t s = seed;
        for (unsigned k = 0; k < 5000u; ++k) {
            int32_t offset = (int32_t)(lcg(&s) % LINE) - LINE / 2;
            spg_action_t r = spg_h(&n, offset, LINE, k);
            if (spg_abs(offset) > SPG_WINDOW) CHECK(r.kind == SPG_NONE);
            CHECK(!n.yield_h);
        }
    }
    /* Camera present but half the pulses are noise: still re-anchors. */
    spg_t m = {0};
    uint32_t s = 7u;
    bool jumped = false;
    for (unsigned k = 0; k < 200u && !jumped; ++k) {
        a = spg_h(&m, (k & 1u) ? (int32_t)(lcg(&s) % LINE) - LINE / 2 : 300 + (int32_t)(lcg(&s) % 21u) - 10,
                  LINE, k);
        jumped = a.kind == SPG_JUMP_H;
    }
    CHECK(jumped && a.value >= 280 && a.value <= 320);

    /* Fine trim only when aligned and the mean is >= SPG_TRIM_MIN. */
    spg_t t = {0};
    for (unsigned k = 0; k < SPG_TRIM_N; ++k) a = spg_h(&t, 3, LINE, k);
    CHECK(a.kind == SPG_NONE);
    for (unsigned k = 0; k < SPG_TRIM_N; ++k) a = spg_h(&t, -12, LINE, 100u + k);
    CHECK(a.kind == SPG_JUMP_H && a.value == -12 && !t.yield_h);

    /* V: one wrong field is not enough, two identical ones a whole field
     * apart are; a correct field confirms. Not field-spaced: no jump. V is
     * ignored while H is yielding. */
    const uint64_t F = 333667u; /* NTSC field, ticks */
    spg_t v = {0};
    CHECK(spg_v(&v, 7, 10u, F).kind == SPG_NONE);
    CHECK(spg_v(&v, 3, 10u + F, F).kind == SPG_NONE);
    a = spg_v(&v, 3, 10u + 2u * F + 40u, F);
    CHECK(a.kind == SPG_JUMP_V && a.value == 3 && v.yield_v);
    spg_reset(&v);
    CHECK(spg_v(&v, 0, 10u + 3u * F, F).kind == SPG_NONE && v.yield_v);
    CHECK(spg_v(&v, 0, 10u + 4u * F, F).kind == SPG_NONE && !v.yield_v);
    spg_t w = {0};
    CHECK(spg_v(&w, 5, 1000u, F).kind == SPG_NONE);
    CHECK(spg_v(&w, 5, 1000u + F / 2u, F).kind == SPG_NONE);      /* half a field: noise */
    CHECK(spg_v(&w, 5, 1000u + F / 2u + 6u * F, F).kind == SPG_NONE); /* > SPG_V_FIELDS */
    CHECK(!w.yield_v);
    v.yield_h = true;
    CHECK(spg_v(&v, 5, 20u * F, F).kind == SPG_NONE && spg_v(&v, 5, 21u * F, F).kind == SPG_NONE);
    /* Acquisition needs aligned H and confirmed V, both current, no yield. */
    spg_t q = {0};
    for (unsigned k = 0; k < SPG_LOCK; ++k) spg_h(&q, 2, LINE, 1000u * k);
    CHECK(!spg_acquired(&q, 11000u));
    spg_v(&q, 0, F, F); CHECK(!spg_acquired(&q, F));
    spg_v(&q, 0, 2u * F, F);
    CHECK(!spg_acquired(&q, 2u * F));             /* H confidence from 2 fields ago: stale */
    spg_h(&q, 2, LINE, 2u * F + 500u);            /* > 1 field later: confidence restarts */
    CHECK(q.aligned == 1u && !spg_acquired(&q, 2u * F + 500u));
    for (unsigned k = 1; k < SPG_LOCK; ++k) spg_h(&q, 2, LINE, 2u * F + 500u + 1271u * k);
    uint64_t now = 2u * F + 500u + 1271u * (SPG_LOCK - 1u);
    CHECK(spg_acquired(&q, now));
    CHECK(!spg_acquired(&q, now + SPG_H_FRESH_TICKS + 1u)); /* no H since: not current */
    CHECK(!spg_acquired(&q, 2u * F + SPG_V_FRESH_TICKS + 1u)); /* V too old */

    /* Expiry: a yield without evidence for > SPG_HOLD_TICKS ends. */
    spg_t e = {0};
    for (unsigned k = 0; k < SPG_LOCK; ++k) spg_h(&e, 400, LINE, 5000u);
    CHECK(spg_yield(&e, 5000u + SPG_HOLD_TICKS));
    CHECK(!spg_yield(&e, 5001u + SPG_HOLD_TICKS));
    printf("PASS: %u camera-phase guard checks\n", checks);
    return 0;
}
