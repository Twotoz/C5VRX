/* C5VRX by Twotoz and the C5VRX contributors: AUTO OUTPUT policy
 * (output_auto.h) and the affine output word (output_level.h). Host logic
 * only: no voltage, goggle or board claim. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "output_auto.h"

static unsigned rnd(unsigned *s) { *s = *s * 1103515245u + 12345u; return *s >> 16; }

/* Levels of a VTX in DAC code x4: porch at code 21 (-1 MHz), sync depth
 * deviation x 1914 kHz, white 2.5 x depth above the porch (white_scale: a
 * VTX with more picture swing, issue #200). */
typedef struct { unsigned tip, top, depth; } levels_t;
static levels_t vtx(double deviation, double white_scale, double porch_code)
{
    const double depth = deviation * 1914.0 * 63.0 / 15000.0;
    levels_t l = {(unsigned)(4.0 * (porch_code - depth) + 0.5), (unsigned)(4.0 * (porch_code + 2.5 * depth * white_scale) + 0.5),
                  (unsigned)(4.0 * depth + 0.5)};
    return l;
}

/* Feed `batches` batches with a little measurement noise; `misses` windows
 * without evidence precede every qualified one (0: a strong carrier, 2: one
 * window in three qualifies, a weak one). Returns the number of applications
 * and leaves the applied word in the state. */
static unsigned feed_q(output_auto_t *a, levels_t l, unsigned batches, unsigned misses, int64_t *now, unsigned *seed,
                       bool apply_ok)
{
    unsigned applied = 0;
    for (unsigned k = 0; k < batches * OA_BATCH; ++k) {
        for (unsigned m = 0; m < misses; ++m) { *now += 55000; output_auto_miss(a); }
        *now += 55000;   /* ~18 windows a second */
        unsigned word = 0;
        const unsigned j = rnd(seed) % 3u;
        if (output_auto_observe(a, l.tip + j - 1u, l.top + j - 1u, l.depth + (rnd(seed) & 1u), *now, &word)) {
            assert(output_is_affine(word));
            if (apply_ok) { output_auto_applied(a, word, *now); ++applied; }
        }
    }
    return applied;
}
static unsigned feed(output_auto_t *a, levels_t l, unsigned batches, int64_t *now, unsigned *seed, bool apply_ok)
{
    return feed_q(a, l, batches, 0u, now, seed, apply_ok);
}

/* Output codes x4 of the exact (quarter code) source levels. */
static void mapped(unsigned word, levels_t l, double *tip, double *porch, double *white)
{
    const double g = output_affine_gain_q3(word) / 8.0, t = output_affine_tip_x4(word) / 4.0;
    *tip = OUTPUT_AFFINE_TIP + g * (l.tip / 4.0 - t);
    *porch = OUTPUT_AFFINE_TIP + g * ((l.tip + l.depth) / 4.0 - t);
    *white = OUTPUT_AFFINE_TIP + g * (l.top / 4.0 - t);
}

int main(void)
{
    unsigned seed = 7, checks = 0;
    int64_t now = 1000000;
    /* 1. The affine word: identity gain keeps every code relative to the tip,
     * metadata bits are untouched, saturation stays inside the DAC. */
    for (unsigned g = OA_GAIN_MIN_Q3; g <= OA_GAIN_MAX_Q3; ++g)
        for (unsigned tip = 0; tip < 40u; ++tip)
            for (unsigned c = 0; c < 64u; ++c) {
                bool clipped;
                const uint16_t out = output_entry((uint16_t)(0xabc0u | c), output_affine(g, tip), &clipped);
                assert((out & 0xffc0u) == 0xabc0u);
                const double want = OUTPUT_AFFINE_TIP + g / 8.0 * ((double)c - (double)tip);
                const double sat = want < 0 ? 0 : want > 63 ? 63 : want;
                assert(abs((int)(out & 63u) - (int)(sat + 0.5)) <= 1);
                assert(clipped == (want < -0.5 || want >= 63.5) || abs((int)(want * 8) % 8) == 4);
                ++checks;
            }
    assert((output_entry(17, output_affine(8, 17), NULL) & 63u) == OUTPUT_AFFINE_TIP);
    /* The quarter-code tip: the word keeps it, whole codes are unchanged. */
    for (unsigned g = OA_GAIN_MIN_Q3; g <= OA_GAIN_MAX_Q3; ++g)
        for (unsigned t4 = 0; t4 < 253u; ++t4) {
            const unsigned w = output_affine_x4(g, t4);
            assert(output_is_affine(w) && output_affine_gain_q3(w) == g && output_affine_tip_x4(w) == t4 &&
                   output_affine_tip(w) == t4 / 4u);
            if (!(t4 & 3u)) assert(w == output_affine(g, t4 / 4u));
            for (unsigned c = 0; c < 64u; c += 7u) {
                const double want = OUTPUT_AFFINE_TIP + g / 8.0 * ((double)c - t4 / 4.0);
                const double sat = want < 0 ? 0 : want > 63 ? 63 : want;
                assert(abs((int)(output_entry((uint16_t)c, w, NULL) & 63u) - (int)(sat + 0.5)) <= 1);
                ++checks;
            }
        }
    /* 2. A low-deviation VTX (0.69, the board's): settles once, after two
     * strong batches, on the reference levels, with the tip well clear of
     * code 0 (research/output_tip_headroom.py). */
    {
        output_auto_t a; output_auto_reset(&a);
        const levels_t l = vtx(0.69, 1.0, 21.0);
        assert(feed(&a, l, 1, &now, &seed, true) == 0);          /* one batch decides nothing */
        assert(feed(&a, l, 1, &now, &seed, true) == 1 && !a.provisional);
        double tip, porch, white;
        mapped(a.word, l, &tip, &porch, &white);
        printf("deviation 0.69: gain %.3fx tip %.1f porch %.1f white %.1f (1x: sync %.1f codes)\n",
               output_affine_gain_q3(a.word) / 8.0, tip, porch, white, l.depth / 4.0);
        assert((output_entry((uint16_t)((l.tip + 2u) / 4u - 5u), a.word, NULL) & 63u) > 0u);
        /* Steady signal: no further restart, however long. */
        assert(feed(&a, l, 400, &now, &seed, true) == 0);
        ++checks;
    }
    /* 3. The invariant: every VTX deviation comes out on the same sync tip,
     * blanking and (standard) white, a wide one included (gain under 1.0x). */
    for (int d = 45; d <= 170; d += 5) {
        output_auto_t a; output_auto_reset(&a);
        const levels_t l = vtx(d / 100.0, 1.0, 21.0);
        const unsigned n = feed(&a, l, 3, &now, &seed, true);
        double tip = l.tip / 4.0, porch = (l.tip + l.depth) / 4.0, white = l.top / 4.0;
        if (a.word) mapped(a.word, l, &tip, &porch, &white);
        if (d == 45 || d == 100 || d == 170)
            printf("deviation %.2f: gain %.3fx tip %.1f porch %.1f white %.1f\n", d / 100.0,
                   a.word ? output_affine_gain_q3(a.word) / 8.0 : 1.0, tip, porch, white);
        assert(n <= 1u);
        /* A quarter source code of measurement jitter is 0.9 output codes at 3.6x. */
        assert(tip >= OUTPUT_AFFINE_TIP - 1.0 && tip <= OUTPUT_AFFINE_TIP + 1.0);
        assert(porch - tip >= OA_SYNC_X4 / 4.0 - 1.0 && porch - tip <= OA_SYNC_X4 / 4.0 + 1.0);
        assert(white >= 56.0 && white <= 63.0);
        ++checks;
    }
    /* 4. More picture swing than the standard (issue #200 levels): the DAC
     * bounds the gain, white stays on it, the tip stays on its code. */
    {
        output_auto_t a, b; output_auto_reset(&a); output_auto_reset(&b);
        const levels_t l = vtx(0.69, 1.58, 18.9);
        assert(feed(&a, l, 2, &now, &seed, true) == 1);
        (void)feed(&b, vtx(0.69, 1.0, 18.9), 2, &now, &seed, true);
        double tip, porch, white;
        mapped(a.word, l, &tip, &porch, &white);
        printf("issue #200 levels: gain %.3fx tip %.1f porch %.1f white %.1f\n", output_affine_gain_q3(a.word) / 8.0, tip, porch, white);
        assert(white <= 63.5 && tip >= OUTPUT_AFFINE_TIP - 0.75 && output_affine_gain_q3(a.word) < output_affine_gain_q3(b.word));
        ++checks;
    }
    /* 5. Picture content does not set the level: a nearly black scene and a
     * standard one give the same word and changing between them costs no
     * restart. Only white past the DAC lowers the gain, within two batches,
     * and it never comes back up with the scene. */
    {
        output_auto_t a; output_auto_reset(&a);
        levels_t dark = vtx(0.69, 1.0, 21.0);
        dark.top = dark.tip + dark.depth + 8u;
        assert(feed(&a, dark, 2, &now, &seed, true) == 1);
        const unsigned g_dark = output_affine_gain_q3(a.word);
        output_auto_t b; output_auto_reset(&b);
        (void)feed(&b, vtx(0.69, 1.0, 21.0), 2, &now, &seed, true);
        assert(abs((int)g_dark - (int)output_affine_gain_q3(b.word)) <= 1);   /* the depth jitter of feed() */
        assert(feed(&a, vtx(0.69, 1.0, 21.0), 400, &now, &seed, true) == 0);
        assert(feed(&a, dark, 400, &now, &seed, true) == 0);
        const levels_t bright = vtx(0.69, 1.58, 21.0);
        const unsigned n = feed(&a, bright, 3, &now, &seed, true);
        assert(n == 1 && output_affine_gain_q3(a.word) < g_dark);
        const unsigned g_bright = output_affine_gain_q3(a.word);
        assert(feed(&a, vtx(0.69, 1.0, 21.0), 400, &now, &seed, true) == 0 && output_affine_gain_q3(a.word) == g_bright);
        assert(feed(&a, dark, 400, &now, &seed, true) == 0);
        ++checks;
    }
    /* 6. Scatter (fading, a wrong sync) and missing sync add nothing; a
     * refused application keeps asking without inventing a new state. */
    {
        output_auto_t a; output_auto_reset(&a);
        const levels_t l = vtx(0.69, 1.0, 21.0);
        for (unsigned k = 0; k < 40u * OA_BATCH; ++k) {
            unsigned word;
            now += 55000;
            const unsigned wild = rnd(&seed) % 160u;
            assert(!output_auto_observe(&a, l.tip + wild, l.top, l.depth + wild, now, &word));
        }
        /* Random levels pass the scatter test in ~0.4 % of batches; one such
         * batch alone never applies (two must agree). */
        assert(a.rejected >= 15u && !a.word && a.batches <= 2u);
        unsigned word;
        assert(!output_auto_observe(&a, l.tip, l.top, 4u, now, &word));          /* no sync depth */
        assert(!output_auto_observe(&a, l.top, l.tip, l.depth, now, &word));     /* no span */
        output_auto_reset(&a);
        assert(feed(&a, l, 6, &now, &seed, false) == 0 && !a.word && a.want);    /* transport refused */
        assert(feed(&a, l, 1, &now, &seed, true) == 1);
        ++checks;
    }
    /* 7. A carrier that moved (AFC step, VTX drift): the tip is followed
     * from 3 codes on, and small shifts cost no restart. */
    {
        output_auto_t a; output_auto_reset(&a);
        (void)feed(&a, vtx(0.69, 1.0, 21.0), 2, &now, &seed, true);
        assert(feed(&a, vtx(0.69, 1.0, 22.5), 30, &now, &seed, true) == 0);
        assert(feed(&a, vtx(0.69, 1.0, 25.0), 3, &now, &seed, true) == 1);
        ++checks;
    }
    /* 8. Hold: once set, weak evidence changes nothing, whatever it says (a
     * compressed sync, noise as white, a pulled tip), for ~20 minutes; the
     * strong signal that returns finds its level untouched. */
    {
        output_auto_t a; output_auto_reset(&a);
        const levels_t l = vtx(0.69, 1.0, 21.0);
        (void)feed(&a, l, 2, &now, &seed, true);
        const unsigned word = a.word;
        levels_t bad = l;
        bad.depth = l.depth * 6u / 10u; bad.top = l.top + 40u; bad.tip = l.tip - 14u;
        assert(feed_q(&a, bad, 400, 2u, &now, &seed, true) == 0 && a.word == word && !a.provisional);
        assert(feed_q(&a, vtx(1.3, 1.6, 24.0), 400, 9u, &now, &seed, true) == 0 && a.word == word);
        assert(feed(&a, l, 400, &now, &seed, true) == 0 && a.word == word);
        ++checks;
    }
    /* 9. A start at the range edge: four agreeing weak batches set the level
     * (three do not), it is provisional, more weak evidence leaves it, and
     * two strong batches correct it from one gain step on. A confirmed
     * level then has hysteresis: one gain step never restarts. */
    {
        output_auto_t a; output_auto_reset(&a);
        const levels_t weak = vtx(0.74, 1.0, 21.0), l = vtx(0.69, 1.0, 21.0);
        assert(feed_q(&a, weak, 3, 2u, &now, &seed, true) == 0 && !a.word);
        assert(feed_q(&a, weak, 1, 2u, &now, &seed, true) == 1 && a.provisional);
        const unsigned g_weak = output_affine_gain_q3(a.word);
        assert(feed_q(&a, vtx(1.0, 1.0, 21.0), 100, 2u, &now, &seed, true) == 0);
        assert(feed(&a, l, 1, &now, &seed, true) == 0);
        assert(feed(&a, l, 1, &now, &seed, true) == 1 && !a.provisional);
        assert(output_affine_gain_q3(a.word) == g_weak + 1u);
        assert(feed(&a, weak, 400, &now, &seed, true) == 0 && output_affine_gain_q3(a.word) == g_weak + 1u);
        /* Strong evidence that agrees with a provisional level confirms it. */
        output_auto_reset(&a);
        (void)feed_q(&a, l, 4, 2u, &now, &seed, true);
        assert(a.word && a.provisional);
        assert(feed(&a, l, 2, &now, &seed, true) == 0 && !a.provisional);
        ++checks;
    }
    /* 10. A deeper level (another VTX on the channel, less deviation): up
     * needs four strong batches and 30 s since the last change; down two. */
    {
        output_auto_t a; output_auto_reset(&a);
        (void)feed(&a, vtx(1.0, 1.0, 21.0), 2, &now, &seed, true);
        const unsigned g = output_affine_gain_q3(a.word);
        const levels_t narrow = vtx(0.69, 1.0, 21.0);
        assert(feed(&a, narrow, 20, &now, &seed, true) == 0);          /* 17.6 s: too soon */
        assert(feed(&a, narrow, 20, &now, &seed, true) == 1 && output_affine_gain_q3(a.word) > g);
        assert(feed(&a, vtx(1.0, 1.0, 21.0), 1, &now, &seed, true) == 0);
        assert(feed(&a, vtx(1.0, 1.0, 21.0), 1, &now, &seed, true) == 1 && output_affine_gain_q3(a.word) == g);
        ++checks;
    }
    /* 11. Another decoder on the channel: the level stays (no return to
     * 1.0x), it is provisional and strong evidence may correct it. */
    {
        output_auto_t a; output_auto_reset(&a);
        const levels_t l = vtx(0.69, 1.0, 21.0);
        (void)feed(&a, l, 2, &now, &seed, true);
        const unsigned word = a.word;
        output_auto_requalify(&a);
        assert(a.word == word && a.provisional && !a.want && !a.n);
        levels_t other = l;
        other.tip += 2u;                                                /* the other decoder's tip, half a code */
        assert(feed(&a, other, 4, &now, &seed, true) == 0 && a.word == word && !a.provisional);
        output_auto_requalify(&a);
        other.tip += 6u;
        assert(feed(&a, other, 2, &now, &seed, true) == 1 && a.word != word);
        output_auto_reset(&a);
        output_auto_requalify(&a);
        assert(!a.word && !a.provisional);
        ++checks;
    }
    assert(output_auto_code_x4(-6000) == 0u && output_auto_code_x4(9000) == 252u && output_auto_code_x4(-1000) == 84u &&
           output_auto_code_x4(-9000) == 0u && output_auto_code_x4(12000) == 252u);
    printf("PASS: AUTO OUTPUT affine word and policy (%u checks)\n", checks);
    return 0;
}
