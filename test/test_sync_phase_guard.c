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

    /* V: one wrong field is not enough, two identical ones are; a correct
     * field confirms. V is ignored while H is yielding. */
    spg_t v = {0};
    CHECK(spg_v(&v, 7, 10u).kind == SPG_NONE);
    CHECK(spg_v(&v, 3, 20u).kind == SPG_NONE);
    a = spg_v(&v, 3, 30u);
    CHECK(a.kind == SPG_JUMP_V && a.value == 3 && v.yield_v);
    spg_reset(&v);
    CHECK(spg_v(&v, 0, 40u).kind == SPG_NONE && v.yield_v);
    CHECK(spg_v(&v, 0, 50u).kind == SPG_NONE && !v.yield_v);
    v.yield_h = true;
    CHECK(spg_v(&v, 5, 60u).kind == SPG_NONE && spg_v(&v, 5, 70u).kind == SPG_NONE);

    /* Expiry: a yield without evidence for > SPG_HOLD_TICKS ends. */
    spg_t e = {0};
    for (unsigned k = 0; k < SPG_LOCK; ++k) spg_h(&e, 400, LINE, 5000u);
    CHECK(spg_yield(&e, 5000u + SPG_HOLD_TICKS));
    CHECK(!spg_yield(&e, 5001u + SPG_HOLD_TICKS));
    printf("PASS: %u camera-phase guard checks\n", checks);
    return 0;
}
