/* C5VRX by Twotoz and the C5VRX contributors: the table-driven dg_measure()
 * (main/gain.c) against the per-sample implementation it replaced (verbatim
 * below as ref_measure, main at db9fa2c). Every field must be identical on
 * random windows, edge cases and the board IQ captures in research/data.
 *
 *   cc -O2 -std=c11 -Wall -Wextra -Werror -I../main test_gain_measure.c ../main/gain.c ../main/arc_phy.c -lm
 *   ./a.out ../research/data/walk_2026-10-13 ../research/data/afc_2026-10-18 */
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "gain.h"
#include "phase8_gain_lut.h"

unsigned char phy_param[2048];   /* arc_phy.c reference; unused here */

static uint8_t r_power[256], r_flags[256];
static int abs_i(int v) { return v < 0 ? -v : v; }
static void ref_prepare(void)
{
    for (unsigned raw = 0; raw < 256; ++raw) {
        int q = (int8_t)((raw & 15u) << 4) >> 4;
        int i = (int8_t)(raw & 240u) >> 4;
        int ci = 2 * i + 1, cq = 2 * q + 1;
        int power = (ci * ci + cq * cq + 2) / 4;
        r_power[raw] = (uint8_t)power;
        r_flags[raw] = (power <= 4 ? 1u : 0u) | ((i == -8 || i == 7 || q == -8 || q == 7) ? 2u : 0u);
    }
}

static dg_observation_t ref_measure(const uint8_t *samples, size_t bytes,
                                const uint8_t phase8_lut[256], uint64_t observed_us, bool envelope)
{
    dg_observation_t r = {.observed_us = observed_us};
    if (!samples || !bytes || !phase8_lut) return r;
    ref_prepare();
    uint16_t hist[114] = {0};
    unsigned origin = 0, clip = 0, coherent = 0, repeats = 0;
    uint8_t prior_phase = 0, prior_raw = 0;
    int i_min = 7, i_max = -8, q_min = 7, q_max = -8;
    int64_t sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0, sxp = 0, syp = 0;
    uint64_t spp = 0;
    for (size_t n = 0; n < bytes; ++n) {
        uint8_t raw = samples[n], power = r_power[raw];
        if (envelope) {
            const int x = 2 * ((int8_t)(raw & 240u) >> 4) + 1;
            const int y = 2 * ((int8_t)((raw & 15u) << 4) >> 4) + 1;
            const int p = x * x + y * y;
            sx += x; sy += y;
            sxx += x * x; syy += y * y; sxy += x * y;
            sxp += x * p; syp += y * p;
            spp += (unsigned)(p * p);
        }
        ++hist[power];
        origin += r_flags[raw] & 1u;
        clip += (r_flags[raw] >> 1) & 1u;
        uint8_t phase = phase8_lut[raw];
        if (n && power >= 8) {
            int delta = (((int)phase - (int)prior_phase + 128) & 255) - 128;
            if (abs_i(delta) <= 32) ++coherent;
        }
        repeats += n && raw == prior_raw;
        if (raw != prior_raw || !n) {
            int si = (int8_t)(raw & 240u) >> 4, sq = (int8_t)((raw & 15u) << 4) >> 4;
            i_min = si < i_min ? si : i_min; i_max = si > i_max ? si : i_max;
            q_min = sq < q_min ? sq : q_min; q_max = sq > q_max ? sq : q_max;
        }
        prior_phase = phase;
        prior_raw = raw;
    }
    unsigned cumulative = 0;
    bool p50 = false, p90 = false;
    unsigned r50 = (unsigned)((bytes + 1u) / 2u);
    unsigned r90 = (unsigned)((bytes * 90u + 99u) / 100u);
    unsigned r95 = (unsigned)((bytes * 95u + 99u) / 100u);
    for (unsigned p = 0; p < 114u; ++p) {
        cumulative += hist[p];
        if (!p50 && cumulative >= r50) { r.p50 = (uint8_t)p; p50 = true; }
        if (!p90 && cumulative >= r90) { r.p90 = (uint8_t)p; p90 = true; }
        if (cumulative >= r95) { r.p95 = (uint8_t)p; break; }
    }
    r.origin_pm = (uint16_t)(origin * 1000u / bytes);
    r.clip_pm = (uint16_t)(clip * 1000u / bytes);
    r.coherence = bytes > 1u ? (uint8_t)(coherent * 100u / (bytes - 1u)) : 0;
    r.repeat_pm = bytes > 1u ? (uint16_t)(repeats * 1000u / (bytes - 1u)) : 0;
    /* Envelope C/N: integer raw moments in the existing sample loop, then
     * translate the moments to the window's DC centre once per probe.
     * No per-sample floating point or extra passes in the fast observer. */
    if (envelope) {
        const double inv = 1.0 / (double)bytes;
        const double mx = (double)sx * inv, my = (double)sy * inv;
        const double dc2 = mx * mx + my * my;
        const double mp_raw = (double)(sxx + syy) * inv;
        const double mp = mp_raw - dc2;
        const double mp2 = (double)spp * inv - 4.0 * (mx * (double)sxp + my * (double)syp) * inv
                    + 4.0 * (mx * mx * (double)sxx + 2.0 * mx * my * (double)sxy + my * my * (double)syy) * inv
                    + 2.0 * dc2 * mp_raw - 3.0 * dc2 * dc2;
        const double var = mp2 - mp * mp;
        const double ratio = mp <= 0.0 ? 0.0 : var > 0.0 ? 100.0 * mp * mp / var : 65535.0;
        r.env_ratio_x100 = (uint16_t)(ratio > 65535.0 ? 65535.0 : ratio + .5);
    }
    int span = i_max - i_min > q_max - q_min ? i_max - i_min : q_max - q_min;
    r.span = (uint8_t)(span < 0 ? 0 : span);
    return r;
}

static unsigned checks, failures;
static bool same(const dg_observation_t *a, const dg_observation_t *b)
{
    return a->p50 == b->p50 && a->p90 == b->p90 && a->p95 == b->p95 && a->origin_pm == b->origin_pm &&
           a->clip_pm == b->clip_pm && a->coherence == b->coherence && a->repeat_pm == b->repeat_pm &&
           a->span == b->span && a->env_ratio_x100 == b->env_ratio_x100 && a->observed_us == b->observed_us;
}
static bool compare(const uint8_t *s, size_t n)
{
    dg_observation_t g = dg_measure_gain(s, n, c5vrx_phase8_gain_lut, 7u);
    dg_observation_t e = dg_measure(s, n, c5vrx_phase8_gain_lut, 7u);
    dg_observation_t rg = ref_measure(s, n, c5vrx_phase8_gain_lut, 7u, false);
    dg_observation_t re = ref_measure(s, n, c5vrx_phase8_gain_lut, 7u, true);
    return same(&g, &rg) && same(&e, &re);
}
static void check(const char *name, bool ok, unsigned cases)
{
    ++checks; failures += !ok;
    printf("%s %s (%u windows)\n", ok ? "ok  " : "FAIL", name, cases);
}

int main(int argc, char **argv)
{
    ref_prepare();
    static uint8_t buf[8192];
    uint32_t seed = 2026;
    unsigned cases = 0; bool ok = true;
    /* Uniform random bytes, every observer/sentinel/calibration size. */
    static const size_t sizes[] = {1, 2, 3, 64, 255, 256, 1024, 4092, 8190};
    for (unsigned z = 0; z < sizeof(sizes) / sizeof(sizes[0]); ++z)
        for (unsigned k = 0; k < 400; ++k) {
            for (size_t j = 0; j < sizes[z]; ++j) { seed = seed * 1664525u + 1013904223u; buf[j] = (uint8_t)(seed >> 24); }
            ok = ok && compare(buf, sizes[z]); ++cases;
        }
    check("random bytes, sizes 1..8190", ok, cases);
    /* Few distinct codes: fixed points, repeats, one axis pinned, rails. */
    cases = 0; ok = true;
    for (unsigned k = 0; k < 4000; ++k) {
        seed = seed * 1664525u + 1013904223u;
        const unsigned codes = 1u + (seed >> 28) % 4u;
        uint8_t pool[4];
        for (unsigned c = 0; c < codes; ++c) { seed = seed * 1664525u + 1013904223u; pool[c] = (uint8_t)(seed >> 24); }
        if (k % 7u == 0) pool[0] = 0x77u;                /* both rails */
        if (k % 11u == 0) pool[codes - 1u] = 0x88u;
        for (size_t j = 0; j < 256; ++j) { seed = seed * 1664525u + 1013904223u; buf[j] = pool[(seed >> 24) % codes]; }
        ok = ok && compare(buf, 256); ++cases;
    }
    check("fixed points, repeats and rails", ok, cases);
    /* A rotating carrier (coherence path) at every step size. */
    cases = 0; ok = true;
    for (int step = -128; step < 128; ++step) {
        for (size_t j = 0; j < 256; ++j) {
            const double a = (double)step * (double)j * 6.283185307179586 / 256.0;
            const int i = (int)(4.6 * __builtin_cos(a) + 8.0) - 8, q = (int)(4.6 * __builtin_sin(a) + 8.0) - 8;
            buf[j] = (uint8_t)(((unsigned)i & 15u) << 4 | ((unsigned)q & 15u));
        }
        ok = ok && compare(buf, 256); ++cases;
    }
    check("rotating carrier, every phase step", ok, cases);
    /* Board IQ captures (VTX off, edge, close in): 64- and 256-byte windows. */
    cases = 0; ok = true;
    for (int a = 1; a < argc; ++a) {
        DIR *d = opendir(argv[a]);
        if (!d) { printf("FAIL cannot open %s\n", argv[a]); ++failures; continue; }
        struct dirent *e;
        while ((e = readdir(d))) {
            const size_t len = strlen(e->d_name);
            if (len < 4 || strcmp(e->d_name + len - 4, ".bin")) continue;
            char path[1024];
            snprintf(path, sizeof(path), "%s/%s", argv[a], e->d_name);
            FILE *f = fopen(path, "rb");
            if (!f) continue;
            const size_t n = fread(buf, 1, sizeof(buf), f);
            fclose(f);
            for (size_t o = 0; o + 256u <= n; o += 64u) {
                ok = ok && compare(buf + o, 64) && compare(buf + o, 256); cases += 2;
            }
        }
        closedir(d);
    }
    check("board IQ captures", ok && (argc < 2 || cases > 0), cases);
    printf("%s: %u checks, %u failures\n", failures ? "FAIL" : "PASS", checks, failures);
    return failures != 0;
}
