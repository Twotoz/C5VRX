/* SPDX-License-Identifier: GPL-3.0-only */

#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "c5vrx_chroma.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* Composite generator matching the decoder's conventions exactly:
 *   chroma(t) = U*sin(theta) - Venc*cos(theta)   -> decoder yields +U,+V
 *   NTSC burst at 180 degrees; PAL bursts alternate +/-135 degrees tied
 *   to the same line parity as the encoded V-axis sign. The carrier is
 *   phase-continuous across the whole stream (theta advances every
 *   sample), mirroring the decoder's continuous oscillator. */
typedef struct {
    uint8_t buf[1280];
    uint64_t sample_index;
    uint32_t fs;
    uint32_t fb;
} enc_t;

static double theta_at(const enc_t *e, size_t i)
{
    return 2.0 * M_PI * (double)e->fb * (double)(i + e->sample_index) /
           (double)e->fs;
}

static void encode_line(enc_t *e, double U, double V, int pal,
                        unsigned burst_amp, int v_sign, uint8_t luma)
{
    const size_t L = sizeof(e->buf);
    memset(e->buf, 30, L);
    for (size_t i = 0; i < 94u; ++i) e->buf[i] = 0;

    const size_t bstart = L * 86u / 1000u + 24u;
    const double phi_b = pal ? (v_sign > 0 ? 3.0 * M_PI / 4.0
                                           : -3.0 * M_PI / 4.0)
                             : M_PI;
    for (size_t i = 0; i < 48u; ++i) {
        const double th = theta_at(e, bstart + i);
        double s = 30.0 + (double)burst_amp * cos(th + phi_b);
        if (s < 0.0) s = 0.0;
        e->buf[bstart + i] = (uint8_t)(s + 0.5);
    }
    for (size_t i = 210u; i < L; ++i) {
        const double th = theta_at(e, i);
        double s = (double)luma + U * sin(th) - ((double)V * (double)v_sign) *
                                                             cos(th);
        if (s < 0.0) s = 0.0;
        if (s > 63.0) s = 63.0;
        e->buf[i] = (uint8_t)(s + 0.5);
    }
    e->sample_index += L;
}

static void decode_avg(c5vrx_chroma_t *c, enc_t *e, double U, double V,
                       int pal, int *u_out, int *v_out)
{
    uint8_t y[1280];
    int8_t u[640], v[640];
    int acc_u = 0, acc_v = 0;
    int prev_sign = 1;
    for (int line = 0; line < 9; ++line) {
        const int v_sign = pal && (line & 1) ? -1 : 1;
        encode_line(e, U, V, pal, 20, v_sign, 40);
        c5vrx_chroma_process_line(c, e->buf,
                                  line ? NULL : NULL,
                                  sizeof(e->buf), y, u, v);
        if (line >= 6) {
            /* Average the stable tail. */
            for (unsigned k = 40u; k < 200u; ++k) {
                acc_u += u[k];
                acc_v += v[k];
            }
        }
        prev_sign = v_sign;
        (void)prev_sign;
    }
    *u_out = acc_u / (3 * 160);
    *v_out = acc_v / (3 * 160);
}

int main(void)
{
    /* ---- NTSC hues: signs exact, ratios preserved, magnitudes sane. */
    static const struct { double U, V; } hues[] = {
        {20, 0}, {0, 20}, {15, 15}, {-18, 10}, {10, -16},
    };
    for (unsigned h = 0; h < sizeof(hues) / sizeof(hues[0]); ++h) {
        c5vrx_chroma_config_t cfg = {
            .sample_rate_hz = 1000000u, .burst_freq_hz = 250000u,
            .line_samples = 1280u, .pal = 0,
        };
        c5vrx_chroma_t c;
        assert(c5vrx_chroma_init(&c, &cfg) == 0);
        enc_t e = {.fs = 1000000u, .fb = 250000u};
        int u = 0, v = 0;
        decode_avg(&c, &e, hues[h].U, hues[h].V, 0, &u, &v);
        printf("ntsc hue %u: U=%+.0f V=%+.0f -> u=%d v=%d\n", h,
               hues[h].U, hues[h].V, u, v);
        if (hues[h].U != 0.0) assert((u > 0) == (hues[h].U > 0));
        if (hues[h].V != 0.0) assert((v > 0) == (hues[h].V > 0));
        /* Common per-unit gain across channels and hues proves a
         * phase-correct quadrature basis (no rotation leakage). */
        double ref_gain = -1.0;
        if (hues[h].U != 0.0) {
            const double g = (double)abs(u) /
                             (double)abs((int)hues[h].U);
            assert(g > 0.6 && g < 2.2);
            if (ref_gain < 0.0) ref_gain = g;
            else assert(fabs(g - ref_gain) < 0.45 * ref_gain);
        }
        if (hues[h].V != 0.0) {
            const double g = (double)abs(v) /
                             (double)abs((int)hues[h].V);
            assert(g > 0.6 && g < 2.2);
            if (ref_gain < 0.0) ref_gain = g;
            else assert(fabs(g - ref_gain) < 0.45 * ref_gain);
        }
    }

    /* ---- PAL V-axis alternation must be undone: V output stays put. */
    {
        c5vrx_chroma_config_t cfg = {
            .sample_rate_hz = 1000000u, .burst_freq_hz = 250000u,
            .line_samples = 1280u, .pal = 1,
        };
        c5vrx_chroma_t c;
        assert(c5vrx_chroma_init(&c, &cfg) == 0);
        enc_t e = {.fs = 1000000u, .fb = 250000u};
        uint8_t y[1280];
        int8_t u[640], v[640];
        int vs[6] = {0};
        for (int line = 0; line < 12; ++line) {
            const int v_sign = (line & 1) ? -1 : 1;
            encode_line(&e, 14.0, 18.0, 1, 20, v_sign, 42);
            c5vrx_chroma_process_line(&c, e.buf, NULL,
                                      sizeof(e.buf), y, u, v);
            if (line >= 6) vs[line - 6] = v[120];
        }
        printf("pal v-track: %d %d %d %d %d %d flips=%llu\n",
               vs[0], vs[1], vs[2], vs[3], vs[4], vs[5],
               (unsigned long long)c.switch_flips);
        for (int k = 1; k < 6; ++k) assert(vs[k] * vs[0] > 0);
        assert(c.switch_flips >= 8u);
    }

    /* ---- Color killer: no burst means clean grayscale. */
    {
        c5vrx_chroma_config_t cfg = {
            .sample_rate_hz = 1000000u, .burst_freq_hz = 250000u,
            .line_samples = 1280u, .pal = 0,
        };
        /* Decoder output contract: one half-rate U/V pair per two
         * ACTIVE-region samples -> (count - astart) / 2 entries. */
        const unsigned out_count = (1280u - 210u) / 2u;
        c5vrx_chroma_t c;
        assert(c5vrx_chroma_init(&c, &cfg) == 0);
        enc_t e = {.fs = 1000000u, .fb = 250000u};
        uint8_t y[1280];
        int8_t u[640], v[640];
        memset(u, 0x7f, sizeof(u)); /* Poison: unwritten must fail loud. */
        memset(v, 0x7f, sizeof(v));
        bool locked_last = true;
        for (int line = 0; line < 12; ++line) {
            encode_line(&e, 22.0, -14.0, 0, 0, 1, 45); /* No burst. */
            locked_last = c5vrx_chroma_process_line(
                &c, e.buf, NULL, sizeof(e.buf), y, u, v);
        }
        assert(!locked_last);
        assert(!c.color_lock);
        for (unsigned k = 0; k < out_count; ++k) {
            assert(u[k] == 0 && v[k] == 0);
        }
        /* Passthrough luma in the active region. */
        for (size_t i = 300u; i < 1280u; ++i) assert(y[i] == e.buf[i]);
        printf("killer: ok bursts_missing=%llu\n",
               (unsigned long long)c.bursts_missing);
    }

    /* ---- Luma fidelity under strong chroma: locked Y must track a
     * luma staircase within a few codes; without burst lock the raw
     * chroma rides straight through into Y. */
    {
        c5vrx_chroma_config_t cfg = {
            .sample_rate_hz = 1000000u, .burst_freq_hz = 250000u,
            .line_samples = 1280u, .pal = 0,
        };
        c5vrx_chroma_t cl, ck;
        assert(c5vrx_chroma_init(&cl, &cfg) == 0);
        assert(c5vrx_chroma_init(&ck, &cfg) == 0);
        enc_t e = {.fs = 1000000u, .fb = 250000u};
        uint8_t yl[1280], yk[1280], prev[1280];
        int8_t u[640], v[640];
        memcpy(prev, e.buf, sizeof(prev));
        int worst_locked = 0;
        unsigned unlocked_ripple = 0;
        static const uint8_t steps[4] = {40, 55, 25, 48};
        for (int line = 0; line < 10; ++line) {
            const unsigned bamp = 20u;
            /* Staircase luma inside encode via post-fix of active area. */
            encode_line(&e, 24.0, 0.0, 0, bamp, 1, 40);
            for (size_t i = 210u; i < 1280u; ++i)
                e.buf[i] = (uint8_t)(steps[(i - 210u) / 268u]);
            const bool have_prev = line >= 1;
            c5vrx_chroma_process_line(&cl, e.buf,
                                      have_prev ? prev : NULL,
                                      sizeof(e.buf), yl, u, v);
            memcpy(prev, e.buf, sizeof(prev));
            if (line >= 6) {
                static const size_t probes[4] = {300, 500, 700, 1000};
                for (int p = 0; p < 4; ++p) {
                    /* Expected value uses the writer's own mapping. */
                    const uint8_t want =
                        steps[(probes[p] - 210u) / 268u];
                    const int d = abs((int)yl[probes[p]] - (int)want);
                    if (d > worst_locked) worst_locked = d;
                }
            }
            /* Killer variant: identical scene, no burst. */
            encode_line(&e, 24.0, 0.0, 0, 0, 1, 40);
            for (size_t i = 210u; i < 1280u; ++i)
                e.buf[i] = (uint8_t)(steps[(i - 210u) / 268u]);
            c5vrx_chroma_process_line(&ck, e.buf, NULL,
                                      sizeof(e.buf), yk, u, v);
            if (line == 9) {
                unsigned lo = 255u, hi = 0u;
                for (size_t i = 400u; i < 1100u; ++i) {
                    if (yk[i] < lo) lo = yk[i];
                    if (yk[i] > hi) hi = yk[i];
                }
                unlocked_ripple = hi - lo;
            }
        }
        printf("fidelity: worst_locked_dev=%d unlocked_ripple=%u\n",
               worst_locked, unlocked_ripple);
        assert(worst_locked <= 4);
        assert(unlocked_ripple >= 10u);
    }

    puts("cvbs_chroma_test: PASS");
    return 0;
}
