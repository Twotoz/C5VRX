#pragma once
/* FusionDemod supervisor (C5VRX by Twotoz/contributors). Operator idea
 * 2026-10-09: picture quality should fall gradually with distance instead
 * of a cliff. PAIR (sharp) runs on a good carrier, EDGE+AutoFit (sync-safe)
 * near the range edge. C/N comes from the envelope ratio about the fitted
 * phase second difference (fdemod_phase_cnr_x10). Windows taken across a
 * gain change are not observations (the caller skips them). Down is fast
 * (three-tick median, or two windows below the panic level), up is slow
 * (continuously above the upper threshold), with a minimum dwell. */
#include <stdbool.h>
#include <stdint.h>
#include <math.h>
#include <stddef.h>
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* Thresholds in fdemod_phase_cnr_x10 units. Crossover measured per case
 * with randomized VTX/boards and amplitudes 0.8-1.8x (lane folding),
 * 2026-10-09: EDGE+AutoFit wins below ~12 (PAIR+AF misses sync and shows
 * 0.4 false/line), PAIR+AutoFit is error-free and sharper above ~13. */
#define FUSION_EDGE_BELOW_X10  110   /* to EDGE below 11 (median of three) */
#define FUSION_PANIC_X10       70    /* two windows below 7: EDGE at once */
#define FUSION_PAIR_ABOVE_X10  130   /* to PAIR above 13 ... */
#define FUSION_FIT_ABOVE_X10   150   /* AutoFit accepts fits only above 15 */
#define FUSION_PAIR_HOLD_US    3000000LL  /* ... held for 3 s */
#define FUSION_MIN_DWELL_US    1000000LL

/* Effective C/N from the phase second difference (board 2026-10-09: the
 * envelope estimator reads folded ultrafine IQ as noise, -7.9 dB on a
 * coherence-100 carrier, which kept FusionDemod on EDGE and blocked every
 * AutoFit fit). d2 = phi[n+1] - 2 phi[n] + phi[n-1] cancels the slowly
 * varying FM frequency; white phase noise of variance 1/(2 rho) leaves
 * var(d2) = 3/rho. A robust sigma (1.4826 x median |d2|, median
 * interpolated inside its histogram bin) gives rho; lane folding and
 * clicks damage the phase and count as noise, as they do for the demods.
 * Biased (+~6 dB at 2 dB, saturating near 20 dB with 4-bit phase) but
 * monotone; FusionDemod thresholds are calibrated in these units. */
static inline int fdemod_phase_cnr_x10(const uint8_t *s, size_t n, const uint8_t phase8[256])
{
    if (n < 64) return -99;
    uint32_t hist[129] = {0}, count = 0;
    for (size_t k = 1; k + 1 < n; ++k) {
        int d = (int)phase8[s[k + 1]] - 2 * (int)phase8[s[k]] + (int)phase8[s[k - 1]];
        d = ((d % 256) + 256 + 128) % 256 - 128;
        ++hist[d < 0 ? -d : d]; ++count;
    }
    uint32_t half = count / 2, cum = 0;
    double med = 128.;
    for (unsigned b = 0; b <= 128; ++b) {
        if (cum + hist[b] > half) { med = b - .5 + (double)(half - cum + .5) / hist[b]; break; }
        cum += hist[b];
    }
    if (med < .05) med = .05;
    double sigma = 1.4826 * med * (2 * M_PI / 256), rho = 3. / (sigma * sigma);
    return (int)lrint(100. * log10(rho));
}

/* Rician envelope: ratio = (rho + 1)^2 / (2 rho + 1) for C/N rho. */
static inline int fdemod_cnr_x10(unsigned ratio_x100)
{
    double r = ratio_x100 / 100.0;
    if (r <= 1.0) return -99;
    double rho = (r - 1.0) + sqrt(r * (r - 1.0));
    return (int)lrint(100.0 * log10(rho));
}

typedef struct {
    bool edge;
    uint8_t panic;
    int last[3];
    unsigned n;
    int64_t above_since, switched_us;
    uint32_t to_edge, to_pair;
} fdemod_t;

static inline int fdemod_median3(const int v[3])
{
    int a = v[0], b = v[1], c = v[2];
    return a > b ? (b > c ? b : a > c ? c : a) : (a > c ? a : b > c ? c : b);
}

/* One control tick; returns true when the program must change to f->edge. */
static inline bool fdemod_step(fdemod_t *f, int cnr_x10, int64_t now)
{
    f->last[f->n % 3u] = cnr_x10; ++f->n;
    if (f->n < 3u) return false;
    int med = fdemod_median3(f->last);
    if (cnr_x10 >= FUSION_PAIR_ABOVE_X10) { if (!f->above_since) f->above_since = now; }
    else f->above_since = 0;
    if (now - f->switched_us < FUSION_MIN_DWELL_US) return false;
    f->panic = cnr_x10 < FUSION_PANIC_X10 ? (uint8_t)(f->panic < 255 ? f->panic + 1 : 255) : 0;
    if (!f->edge && (med < FUSION_EDGE_BELOW_X10 || f->panic >= 2)) {
        f->edge = true; f->switched_us = now; ++f->to_edge; return true;
    }
    if (f->edge && f->above_since && now - f->above_since >= FUSION_PAIR_HOLD_US) {
        f->edge = false; f->switched_us = now; ++f->to_pair; return true;
    }
    return false;
}
