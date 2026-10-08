#pragma once
/* FusionDemod supervisor (C5VRX by Twotoz/contributors). Operator idea
 * 2026-10-09: picture quality should fall gradually with distance instead
 * of a cliff. PAIR (sharp) runs on a good carrier, EDGE+AutoFit (sync-safe)
 * near the range edge. C/N comes from the envelope ratio about the fitted
 * DC (predemod_circle_dc): content- and deviation-independent, +-1 dB from
 * 2..14 dB on the search model. Down is fast (three-tick median), up is
 * slow (continuously above the upper threshold), with a minimum dwell. */
#include <stdbool.h>
#include <stdint.h>
#include <math.h>

#define FUSION_EDGE_BELOW_X10  90    /* to EDGE below 9.0 dB */
#define FUSION_PAIR_ABOVE_X10  120   /* to PAIR above 12.0 dB ... */
#define FUSION_PAIR_HOLD_US    3000000LL  /* ... held for 3 s */
#define FUSION_MIN_DWELL_US    1000000LL

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
    if (!f->edge && med < FUSION_EDGE_BELOW_X10) {
        f->edge = true; f->switched_us = now; ++f->to_edge; return true;
    }
    if (f->edge && f->above_since && now - f->above_since >= FUSION_PAIR_HOLD_US) {
        f->edge = false; f->switched_us = now; ++f->to_pair; return true;
    }
    return false;
}
