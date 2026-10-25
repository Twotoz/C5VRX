/* C5VRX-5 by Twotoz and the C5VRX contributors: line TBC host test
 * (main/sync_tbc.h). Synthetic PAL FM with burst, picture, sync-like dark
 * picture blocks and a vertical interval, 4-bit IQ, through the real P64
 * LUT. Host model, not goggle or board acceptance. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include "sync_tbc.h"
#include "phase8_gain_lut.h"
#include "p64_tables.h"

#define LINE 2560
#define LINES 1300
#define N (LINE * LINES)
#define RING (1u << 22)   /* covers the whole record: no wrap in this test */

static uint8_t raw[RING], orig[RING];
static uint8_t region[N];   /* 0 blank, 1 picture, 2 sync, 3 burst, 4 V pulse */
static double edge_true[LINES];
static int is_normal[LINES];
static unsigned checks, failures;
static tbc_tables_t T;
static uint32_t seed = 1;

static void check(const char *name, bool ok)
{
    ++checks; failures += !ok;
    printf("%s %s\n", ok ? "ok" : "FAIL", name);
}
static double uni(void) { seed = seed * 1664525u + 1013904223u; return ((seed >> 8) + 0.5) / 16777216.0; }
static double gauss(void) { return sqrt(-2 * log(uni())) * cos(6.283185307179586 * uni()); }

/* Instantaneous frequency (Hz) of the composite, sample k. */
static double freq_at(long k, int *reg)
{
    const long line = k / LINE, pos = k % LINE, field_line = line % 312;
    *reg = 0;
    if (field_line < 8) {   /* vertical interval: broad pulses then equalising, half-line spaced */
        const long half = pos % (LINE / 2);
        const bool broad = field_line < 3;
        const long width = broad ? (LINE / 2 - 188) : 94;
        if (half < width) { *reg = 4; return -2914e3; }
        return -1000e3;
    }
    if (pos < 188) { *reg = 2; return -2914e3; }
    if (pos >= 224 && pos < 314) {
        *reg = 3;
        return -1000e3 + 957e3 * sin(6.283185307179586 * 4433618.75 * k / 40e6 + (line & 1 ? 2.36 : -2.36));
    }
    if (pos >= 420 && pos < LINE - 66) {
        *reg = 1;
        /* Picture: smooth random luma plus chroma, and one sync-wide dark
         * block between bright ones on every 7th line (must never pass). */
        const long x = pos - 420;
        if (line % 7 == 0 && x >= 600 && x < 790) return -1000e3 + 0.075 * 4786e3;
        const double luma = 0.5 + 0.4 * sin(x * 0.004 + line * 0.3) * cos(x * 0.0011);
        return -1000e3 + luma * 4786e3 + 300e3 * sin(6.283185307179586 * 4433618.75 * k / 40e6 + x * 0.002);
    }
    return -1000e3;
}

static void make(double snr_db, double radius)
{
    double phase = 0.3, sigma = radius / sqrt(2.0) * pow(10.0, -snr_db / 20.0);
    /* Light low-pass on the frequency (VTX/camera bandwidth): edges ~150 ns. */
    double f_lp = -1000e3;
    for (long k = 0; k < N; ++k) {
        int reg;
        double f = freq_at(k, &reg);
        f_lp += (f - f_lp) * 0.35;
        region[k] = (uint8_t)reg;
        phase += 6.283185307179586 * f_lp / 40e6;
        double i = radius * cos(phase) + sigma * gauss(), q = radius * sin(phase) + sigma * gauss();
        int ii = (int)floor(i), qq = (int)floor(q);
        ii = ii < -8 ? -8 : ii > 7 ? 7 : ii;
        qq = qq < -8 ? -8 : qq > 7 ? 7 : qq;
        raw[k] = (uint8_t)(((unsigned)ii & 15u) << 4 | ((unsigned)qq & 15u));
    }
    for (int l = 0; l < LINES; ++l) {
        is_normal[l] = l % 312 >= 8;
        edge_true[l] = (double)l * LINE;   /* the generator's sync start */
    }
    memcpy(orig, raw, N);
}

/* P64 pair codes (research/sim.py _pair_codes, PAIR4411, 4 token bits). */
static void p64(const uint8_t *in, uint8_t *out)
{
    static const int bits[10] = {4, 5, 6, 7, 0, 1, 2, 3, 15, 11};
    unsigned state = 0;
    memset(out, 0, N);
    for (long k = 0; k + 1 < N / 2; ++k) {
        unsigned word = in[2 * k] | (unsigned)in[2 * k + 1] << 8, address = 0;
        for (int j = 0; j < 10; ++j) address |= ((word >> bits[j]) & 1u) << j;
        unsigned token = p64_table[address] >> 12;
        unsigned value = p64_table[(state << 4) + token];
        state = (value >> 6) & 63u;
        out[2 * k + 2] = out[2 * k + 3] = (uint8_t)(value & 63u);
    }
}

/* Leading-edge jitter of the P64 output (4-tap box, interpolated 50 %
 * crossing) on normal lines: rms ns around the per-run mean delay, and the
 * share of lines more than 100 ns off. */
static void edge_jitter(const uint8_t *codes, double *rms_ns, double *bad_pct)
{
    static double e[LINES];
    int n = 0;
    double mean = 0;
    for (int l = 2; l < LINES - 1; ++l) {
        if (!is_normal[l] || !is_normal[l - 1]) continue;
        const long base = (long)edge_true[l] - 40;
        double y[160];
        for (int k = 0; k < 160; ++k) y[k] = (codes[base + k] + codes[base + k + 1] + codes[base + k + 2] + codes[base + k + 3]) / 4.0;
        double lo = 0, hi = 0;
        for (int k = 100; k < 140; ++k) lo += y[k];
        for (int k = 0; k < 20; ++k) hi += y[k];
        lo /= 40; hi /= 20;
        const double mid = (lo + hi) / 2;
        int k = 10;
        while (k < 150 && !(y[k] >= mid && y[k + 1] < mid)) ++k;
        if (k >= 150) continue;
        e[n] = base + k + (y[k] - mid) / (y[k] - y[k + 1]) - edge_true[l];
        mean += e[n++];
    }
    mean /= n;
    double ss = 0;
    int bad = 0, used = 0;
    for (int i = 0; i < n; ++i) {
        const double d = e[i] - mean;
        if (fabs(d) > 4) ++bad;
        if (fabs(d) < 40) { ss += d * d; ++used; }
    }
    *rms_ns = sqrt(ss / used) * 25;
    *bad_pct = 100.0 * bad / n;
}

/* Streams the record through the TBC in 4092-byte steps; TX is modelled
 * LAG bytes behind RX. */
static tbc_t run(bool reference, int32_t ref_sync, int32_t ref_porch, long lag)
{
    tbc_t s;
    tbc_reset(&s, LINE * 65536);
    if (reference) tbc_set_reference(&s, ref_sync, ref_porch);
    tbc_ring_t r = {.ring = raw, .mask = RING - 1u};
    for (long c = 4092; ; c += 4092) {
        r.complete = c < N ? c : N;
        r.write_min = r.complete - lag + 8188;
        for (int k = 0; k < 4; ++k) tbc_run(&T, &s, &r, 6);
        if (c >= N) break;
    }
    return s;
}

/* Every changed byte must lie in [edge - LEAD - 2, edge + TIP_KEEP + 2] of
 * a true normal sync: never in picture, burst, front porch beyond the lead
 * or a vertical-interval pulse. */
static bool only_leading_edges(long *outside, long *changed)
{
    *outside = *changed = 0;
    for (long k = 0; k < N; ++k) {
        if (raw[k] == orig[k]) continue;
        ++*changed;
        const long l = (k + LINE / 2) / LINE, rel = k - (long)edge_true[l];
        if (l >= LINES || !is_normal[l] || rel < -TBC_LEAD - 2 || rel > TBC_TIP_KEEP + 2 || region[k] == 1 ||
            region[k] == 3 || region[k] == 4) {
            ++*outside;
        }
    }
    return *outside == 0;
}

int main(void)
{
    static uint8_t c0[N], c1[N];
    tbc_tables_init(&T, c5vrx_phase8_gain_lut);
    long outside, changed;
    char label[160];

    const double snrs[] = {30.0, 14.0, 11.0, 8.0};
    for (unsigned i = 0; i < sizeof(snrs) / sizeof(snrs[0]); ++i) {
        seed = 7 + i;
        make(snrs[i], 4.2);
        p64(raw, c0);
        double j0, b0, j1, b1;
        edge_jitter(c0, &j0, &b0);
        tbc_t s = run(true, -2914, -1000, 24576);
        p64(raw, c1);
        edge_jitter(c1, &j1, &b1);
        const bool safe = only_leading_edges(&outside, &changed);
        printf("   SNR %4.1f dB: rewritten %lu/%lu lines, leading-edge jitter %.0f -> %.0f ns rms, >100 ns %.1f -> %.1f %%\n",
               snrs[i], (unsigned long)s.rewritten, (unsigned long)s.lines, j0, j1, b0, b1);
        snprintf(label, sizeof(label), "SNR %.0f dB: only leading-edge segments of real H syncs changed (%ld bytes)",
                 snrs[i], changed);
        check(label, safe);
        if (snrs[i] >= 11.0) {
            snprintf(label, sizeof(label), "SNR %.0f dB: locks and rewrites >= 95 %% of normal lines", snrs[i]);
            check(label, s.locks >= 1 && s.rewritten * 100 >= 95u * 1240u * 1u - 0u && s.rewritten <= s.lines);
        }
        snprintf(label, sizeof(label), "SNR %.0f dB: leading-edge jitter not worse (+3 ns) and outliers not more", snrs[i]);
        check(label, j1 <= j0 + 3.0 && b1 <= b0 + 0.1);
        if (snrs[i] <= 11.0) {
            snprintf(label, sizeof(label), "SNR %.0f dB: leading-edge jitter at least halved", snrs[i]);
            check(label, j1 * 2 <= j0);
        }
        /* Colour: P64 output identical in every burst and picture sample. */
        long diff = 0;
        for (long k = 0; k < N; ++k) diff += (region[k] == 1 || region[k] == 3) && k > 300 && c0[k] != c1[k];
        snprintf(label, sizeof(label), "SNR %.0f dB: burst and picture output bit-identical (%ld codes differ)", snrs[i], diff);
        check(label, diff == 0);
    }

    seed = 99;
    make(30.0, 4.2);
    tbc_t s = run(false, 0, 0, 24576);
    check("no AFC reference: never acquires, nothing written", !s.acquisitions && !memcmp(raw, orig, N));
    memcpy(raw, orig, N);
    s = run(true, 1500, 3500, 24576);
    check("reference far from the real levels: nothing written", !s.rewritten && !memcmp(raw, orig, N));
    memcpy(raw, orig, N);
    s = run(true, -2914, -1000, 8000);   /* TX right behind RX: write limit past the data */
    check("TX right behind RX: nothing written behind the write limit", !s.rewritten && !memcmp(raw, orig, N));
    /* Pure receiver noise: no lock, no write. */
    for (long k = 0; k < N; ++k) {
        int ii = (int)floor(3.0 * gauss()), qq = (int)floor(3.0 * gauss());
        ii = ii < -8 ? -8 : ii > 7 ? 7 : ii;
        qq = qq < -8 ? -8 : qq > 7 ? 7 : qq;
        raw[k] = (uint8_t)(((unsigned)ii & 15u) << 4 | ((unsigned)qq & 15u));
    }
    memcpy(orig, raw, N);
    s = run(true, -2914, -1000, 24576);
    check("receiver noise: no lock, nothing written", !s.locks && !s.rewritten && !memcmp(raw, orig, N));

    printf("%s: %u checks, %u failures\n", failures ? "FAIL" : "PASS", checks, failures);
    return failures != 0;
}
