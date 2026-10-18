/* C5VRX by Twotoz/contributors: camera-phase guard decisions. Host logic
 * only; no physical timing or monitor-lock claim. */
#include <assert.h>
#include <stdio.h>
#include "sync_phase_guard.h"

#define LINE 1271
#define PAL_LINE 1280
static unsigned checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)

static uint32_t lcg(uint32_t *s) { *s = *s * 1103515245u + 12345u; return *s >> 8; }

typedef struct { unsigned jumps, trims, stds; int32_t last_jump; } tally_t;

/* One grid line n: the camera (period cam_line, offset cam_off, present
 * with probability presence/100, +-jitter) plus `noise_x4`/4 random H-width
 * pulses, in time order. The grid is n*LINE. */
static void line(spg_t *g, tally_t *t, uint64_t n, double cam_line, int32_t cam_off, unsigned presence,
                 int jitter, unsigned noise_x4, uint32_t *s)
{
    uint64_t at[16]; unsigned count = 0;
    uint64_t base = 1000000u + n * LINE;
    if (presence && lcg(s) % 100u < presence) {
        double c = 1000000.0 + (double)n * cam_line + cam_off;
        int j = jitter ? (int)(lcg(s) % (2u * (unsigned)jitter + 1u)) - jitter : 0;
        if (c + j > (double)(base - LINE / 2) && c + j < (double)(base + LINE / 2)) at[count++] = (uint64_t)(c + j + 0.5);
    }
    for (unsigned k = 0; k < noise_x4 && count < 16u; ++k)
        if (lcg(s) % 4u == 0u) at[count++] = base - LINE / 2 + 1u + lcg(s) % (LINE - 1u);
    for (unsigned a = 1; a < count; ++a)
        for (unsigned b = a; b && at[b - 1] > at[b]; --b) { uint64_t x = at[b]; at[b] = at[b - 1]; at[b - 1] = x; }
    for (unsigned k = 0; k < count; ++k) {
        int32_t offset = spg_wrap((int64_t)at[k] - (int64_t)base, LINE);
        spg_action_t a = spg_h(g, offset, LINE, PAL_LINE, at[k]);
        if (a.kind == SPG_STD) ++t->stds;
        else if (a.kind == SPG_JUMP_H && g->yield_h) { ++t->jumps; t->last_jump = a.value; spg_reset(g); }
        else if (a.kind == SPG_JUMP_H) { ++t->trims; spg_shift(g, a.value); }
    }
}

int main(void)
{
    /* In-phase camera at 30-100 % presence, 0-4 noise pulses per line: held
     * in phase, never re-anchored. The old per-pulse score lost it from one
     * noise pulse per line (range edge) and kept re-anchoring. */
    for (unsigned noise = 0; noise <= 16u; noise += 4u)
        for (unsigned presence = 30; presence <= 100u; presence += 35u) {
            spg_t g = {0}; tally_t t = {0}; uint32_t s = 11u + noise + presence;
            unsigned held = 0;
            for (uint64_t n = 0; n < 4000u; ++n) {
                line(&g, &t, n, LINE, 2, presence, 3, noise, &s);
                if (n >= SPG_ACQ_LINES + 200u) held += spg_in_phase(&g);
            }
            unsigned span = 4000u - SPG_ACQ_LINES - 200u;
            CHECK(!t.jumps && !t.stds && t.trims <= 10u && !g.yield_h);
            /* A 30 % camera in dense static is held most of the time only. */
            CHECK(held >= span * (presence >= 65u || noise <= 4u ? 99u : 75u) / 100u);
            printf("in phase: noise %.2f/line presence %u%%: held %u/%u lines, trims %u, jumps %u\n",
                   noise / 4.0, presence, held, span, t.trims, t.jumps);
        }
    /* Off-grid camera (+300, and near the half-line wrap at +634): one H
     * re-anchor by its offset, in clean signal and in dense static, then
     * held without further jumps. */
    for (unsigned noise = 0; noise <= 12u; noise += 12u)
        for (int32_t off = 300; off <= 634; off += 334) {
            spg_t g = {0}; tally_t t = {0}; uint32_t s = 5u;
            int32_t now = off;
            uint64_t first = 0;
            for (uint64_t n = 0; n < 3000u; ++n) {
                unsigned before = t.jumps;
                line(&g, &t, n, LINE, now, 60u, 3, noise, &s);
                if (t.jumps != before) {
                    if (!first) first = n;
                    CHECK(spg_abs(spg_wrap((int64_t)t.last_jump - now, LINE)) <= 24);
                    now = spg_wrap((int64_t)now - t.last_jump, LINE);
                }
            }
            CHECK(t.jumps == 1u && first <= SPG_ACQ_LINES && spg_in_phase(&g) && !t.stds);
        }
    /* Pure noise (many seeds, 0.25-6 pulses per line): no lock, no yield,
     * no jump, no standard switch (7-sigma threshold on the noise rate). */
    unsigned noise_locks = 0;
    for (uint32_t seed = 1; seed < 60u; ++seed)
        for (unsigned noise = 1; noise <= 24u; noise *= 2u) {
            spg_t n = {0}; tally_t t = {0}; uint32_t s = seed * 977u;
            for (uint64_t k = 0; k < 3000u; ++k) {
                line(&n, &t, k, LINE, 0, 0, 0, noise, &s);
                noise_locks += n.locked || n.alt_locked;
                CHECK(!n.yield_h && !spg_in_phase(&n));
            }
            CHECK(!t.jumps && !t.trims && !t.stds);
        }
    CHECK(!noise_locks);
    printf("pure noise: %u locked lines of %u\n", noise_locks, 59u * 5u * 3000u);
    /* Fine trim: a camera 12 ticks early is trimmed by its mean once held;
     * the guard's camera phase follows the moved grid. */
    {
        spg_t g = {0}; tally_t t = {0}; uint32_t s = 3u;
        int32_t now = -12;
        for (uint64_t n = 0; n < 600u; ++n) {
            unsigned before = t.trims;
            int32_t was = g.cam;
            line(&g, &t, n, LINE, now, 100u, 1, 0, &s);
            if (t.trims != before) {
                CHECK(t.trims == 1u && was - g.cam >= -16 && was - g.cam <= -8);
                now += 12;
            }
        }
        CHECK(t.trims == 1u && !t.jumps && spg_in_phase(&g) && spg_abs(g.cam) <= 4);
    }
    /* A PAL camera (1280-tick lines) on the NTSC grid drifts 9 ticks per
     * line: no in-phase lock, the other-standard histogram reports it. An
     * NTSC camera never does. */
    {
        spg_t g = {0}; tally_t t = {0}; uint32_t s = 9u;
        uint64_t at = 0;
        for (uint64_t n = 0; n < 2000u && !t.stds; ++n) { line(&g, &t, n, PAL_LINE, 100, 60u, 3, 8u, &s); at = n; }
        CHECK(t.stds == 1u && at <= 2u * SPG_ACQ_LINES && !spg_in_phase(&g));
        printf("PAL camera on NTSC grid: standard switch after %llu lines, %u jumps\n",
               (unsigned long long)at, t.jumps);
    }

    /* V: one wrong field is not enough, two identical ones a whole field
     * apart are; a correct field confirms. Not field-spaced: no jump. V is
     * ignored while H is yielding. */
    const uint64_t F = 333667u; /* NTSC field, ticks */
    spg_t v = {0};
    spg_action_t a;
    CHECK(spg_v(&v, 7, 10u, F).kind == SPG_NONE);
    CHECK(spg_v(&v, 3, 10u + F, F).kind == SPG_NONE);
    a = spg_v(&v, 3, 10u + 2u * F + 40u, F);
    CHECK(a.kind == SPG_JUMP_V && a.value == 3 && v.yield_v);
    spg_reset_v(&v);
    CHECK(spg_v(&v, 0, 10u + 3u * F, F).kind == SPG_NONE && v.yield_v);
    CHECK(spg_v(&v, 0, 10u + 4u * F, F).kind == SPG_NONE && !v.yield_v);
    spg_t w = {0};
    CHECK(spg_v(&w, 5, 1000u, F).kind == SPG_NONE);
    CHECK(spg_v(&w, 5, 1000u + F / 2u, F).kind == SPG_NONE);      /* half a field: noise */
    CHECK(spg_v(&w, 5, 1000u + F / 2u + 6u * F, F).kind == SPG_NONE); /* > SPG_V_FIELDS */
    CHECK(!w.yield_v);
    v.yield_h = true;
    CHECK(spg_v(&v, 5, 20u * F, F).kind == SPG_NONE && spg_v(&v, 5, 21u * F, F).kind == SPG_NONE);

    /* Acquisition needs H held in phase and confirmed V, both current. */
    {
        spg_t q = {0}; tally_t t = {0}; uint32_t s = 1u;
        for (uint64_t n = 0; n < SPG_ACQ_LINES; ++n) line(&q, &t, n, LINE, 1, 100u, 1, 0, &s);
        uint64_t now = q.h_tick;
        CHECK(spg_in_phase(&q) && !spg_acquired(&q, now));
        spg_v(&q, 0, now - F, F); CHECK(!spg_acquired(&q, now));
        spg_v(&q, 0, now, F);
        CHECK(spg_acquired(&q, now));
        CHECK(!spg_acquired(&q, now + SPG_H_FRESH_TICKS + 1u)); /* no H since: not current */
        /* A gap longer than SPG_GAP_LINES: the old phase is no evidence. */
        uint64_t n = SPG_ACQ_LINES + SPG_GAP_LINES + 10u;
        line(&q, &t, n, LINE, 1, 100u, 0, 0, &s);
        line(&q, &t, n + 1u, LINE, 1, 100u, 0, 0, &s);
        CHECK(!spg_in_phase(&q) && !spg_acquired(&q, q.h_tick + 1u));
    }

    /* Expiry: a yield without evidence for > SPG_HOLD_TICKS ends. */
    spg_t e = {0};
    e.yield_h = true; e.last_evidence = 5000u;
    CHECK(spg_yield(&e, 5000u + SPG_HOLD_TICKS));
    CHECK(!spg_yield(&e, 5001u + SPG_HOLD_TICKS));
    printf("PASS: %u camera-phase guard checks\n", checks);
    return 0;
}
