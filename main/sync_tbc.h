/* C5VRX-5 by Twotoz and the C5VRX contributors: sample-domain line TBC.
 *
 * Why: a decoder without a time-base corrector (HDZero goggles: TP2825)
 * line-locks on every H-sync edge. Demodulated 4-bit IQ puts the noise of
 * the FM channel on those edges: in the host model (research/tbc_study.py,
 * real P64 LUT) the leading edge moves 50-125 ns rms per line at 8-12 dB
 * C/N, with outliers; an analog goggle averages that, a TP2825 shifts the
 * lines. An inline TBC fixed it on the board (operator report 2026-10-25).
 *
 * What: the camera's line period is crystal-stable and the receive chain is
 * clock-locked (RX and TX: PLL_F240M / 6), so the H grid is a straight line
 * in absolute sample counts. A slow second-order PLL follows the measured
 * leading edges. On every line where the real pulse is verified to be a
 * normal H sync (normal width, sync level inside, back at blanking after
 * it), the raw IQ of that pulse is replaced, ahead of the TX read, by a
 * clean constant-frequency rotation at the measured porch and tip
 * frequencies, with raised-cosine edges placed at the PLL time and the
 * smoothed width. The segment starts in the front porch and ends before the
 * colour burst, phase-continuous at both ends, so burst, picture and the
 * vertical interval keep the camera's own samples (colour is untouched).
 *
 * What it never does (lessons of the removed flywheel): no pulse is
 * invented where the camera has none, no CPU interrupt time decides an
 * output edge, and nothing is written into data the RX has already
 * overwritten or the TX may already be reading. Without lock it passes the
 * signal through unchanged.
 *
 * Pure (no IDF): positions are absolute sample indices into a power-of-two
 * cyclic byte ring; the caller supplies the ring and the write limits.
 * Host test: test/test_sync_tbc.c; model and study: research/tbc_study.py. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <math.h>

/* Phase8 LUT units: 256 codes per turn; one code per sample = 156.25 kHz. */
#define TBC_WIN_PRE      64   /* samples before the predicted edge in the analysis window */
#define TBC_WIN_LEN      352
#define TBC_BOX          8    /* centred boxcar on the phase steps */
#define TBC_WIDTH_MIN    160  /* 4.0 us */
#define TBC_WIDTH_MAX    225  /* 5.6 us */
#ifndef TBC_LEAD
#define TBC_LEAD 40   /* segment starts 1.0 us before the edge (front porch) */
#endif
#ifndef TBC_TIP_KEEP
#define TBC_TIP_KEEP 112  /* and ends 2.8 us into the tip, 1.9 us before the real trailing edge */
#endif
#define TBC_SEG_MAX      (TBC_LEAD + TBC_TIP_KEEP)
#ifndef TBC_RISE_X16
#define TBC_RISE_X16 64   /* edge rise 4 samples (100 ns), in 1/16 sample */
#endif
#define TBC_GATE_LOCKED  8    /* samples */
#define TBC_GATE_ACQ     30
#define TBC_LOCK_LINES   16
#define TBC_UNLOCK_MISSES 48  /* longer than a vertical interval (NTSC 9, PAL 7.5 lines) */
#define TBC_REWRITE_ERR  6    /* only rewrite lines measured this close to the PLL */
#define TBC_MIN_DEPTH_X16 (4 * 16)

typedef struct {
    uint8_t phase[256];          /* Phase8 LUT */
    uint8_t power[256];          /* afc2_power: < 2 = origin cell, no phase */
    uint8_t radius_x4[256];
    uint8_t best[6][256];        /* byte of radius 2..7 cells nearest each phase code */
    int16_t ramp_q12[TBC_RISE_X16 + 1];
} tbc_tables_t;

typedef struct {
    /* PLL, absolute sample index in Q16. */
    int64_t next_q16;            /* predicted leading edge of the next line */
    int32_t period_q16;
    int32_t nominal_q16;
    bool have, locked;
    unsigned good, misses;
    /* Smoothed line levels (phase steps x16) and width (Q4 samples). */
    int32_t porch_x16, tip_x16, width_q4;
    bool levels;
    /* Reference levels from the AFC's burst/line-timing confirmed windows
     * (tbc_set_reference); no acquisition without them. */
    int32_t ref_porch_x16, ref_tip_x16;
    bool ref;
    /* Counters. */
    uint32_t lines, measured, rewritten, refused_late, refused_shape, locks, unlocks, acquisitions;
    int32_t last_err_q8;
} tbc_t;

static inline int tbc_wrap8(int d) { return ((d + 128) & 255) - 128; }

static inline void tbc_tables_init(tbc_tables_t *t, const uint8_t phase_lut[256])
{
    memcpy(t->phase, phase_lut, 256);
    for (unsigned b = 0; b < 256; ++b) {
        int i = (int)(int8_t)(b & 0xF0u) >> 4, q = (int)(int8_t)((b & 0x0Fu) << 4) >> 4;
        int ci = 2 * i + 1, cq = 2 * q + 1;
        t->power[b] = (uint8_t)((unsigned)(ci * ci + cq * cq + 2) / 4u);
        /* radius of the cell centre, x4, integer sqrt */
        unsigned r2 = (unsigned)(ci * ci + cq * cq) * 4u, r = 0;   /* (2|z|)^2 * 4 = (4|z|)^2 */
        while ((r + 1u) * (r + 1u) <= r2) ++r;
        t->radius_x4[b] = (uint8_t)r;
    }
    for (unsigned rad = 2; rad <= 7; ++rad)
        for (unsigned c = 0; c < 256; ++c) {
            int best = -1, best_err = 1 << 20;
            for (unsigned b = 0; b < 256; ++b) {
                int dr = (int)t->radius_x4[b] - (int)(rad * 4u);
                if (dr < -3 || dr > 3) continue;
                int e = tbc_wrap8((int)t->phase[b] - (int)c);
                e = (e < 0 ? -e : e) * 16 + (dr < 0 ? -dr : dr);
                if (e < best_err) { best_err = e; best = (int)b; }
            }
            t->best[rad - 2][c] = (uint8_t)best;
        }
    /* Raised cosine 0..4096 across the rise, in 1/16 sample (init only). */
    for (int k = 0; k <= TBC_RISE_X16; ++k)
        t->ramp_q12[k] = (int16_t)lround(4096.0 * (0.5 - 0.5 * cos(3.141592653589793 * k / TBC_RISE_X16)));
}

static inline void tbc_reset(tbc_t *s, int32_t nominal_q16)
{
    memset(s, 0, sizeof(*s));
    s->nominal_q16 = nominal_q16;
    s->period_q16 = nominal_q16;
}

/* AFC sync tip and porch (kHz of the demodulated frequency). */
static inline void tbc_set_reference(tbc_t *s, int32_t sync_khz, int32_t porch_khz)
{
    s->ref_tip_x16 = sync_khz * 256 / 2500;     /* 156.25 kHz per step, x16 */
    s->ref_porch_x16 = porch_khz * 256 / 2500;
    s->ref = porch_khz - sync_khz >= 600;
}


/* Raised-cosine step at (time - edge) in Q16 samples, Q12 result: 0 before
 * the rise, 4096 after it, centred on the edge. */
static inline int32_t tbc_ramp(const tbc_tables_t *t, int64_t rel_q16)
{
    const int64_t i = ((rel_q16 * 16) >> 16) + TBC_RISE_X16 / 2;   /* 1/16 sample from rise start */
    if (i <= 0) return 0;
    if (i >= TBC_RISE_X16) return 4096;
    return t->ramp_q12[i];
}

typedef struct {
    uint8_t *ring;
    uint32_t mask;            /* ring bytes - 1 (power of two) */
    int64_t complete;         /* every sample < complete is written by RX */
    int64_t write_min;        /* writes must start at or after this (ahead of TX) */
} tbc_ring_t;

static inline uint8_t tbc_at(const tbc_ring_t *r, int64_t n) { return r->ring[(uint32_t)n & r->mask]; }

/* Smoothed phase steps around a predicted edge. v[k] is x16 the mean step
 * between samples base+k and base+k+1 (centred boxcar); ok[k] false without
 * phase. Returns false when no step in the window has phase. */
static inline bool tbc_steps(const tbc_tables_t *t, const tbc_ring_t *r, int64_t base, int16_t *v, bool *ok)
{
    int32_t sum[TBC_WIN_LEN + 1], cnt[TBC_WIN_LEN + 1];
    sum[0] = cnt[0] = 0;
    uint8_t prev = tbc_at(r, base);
    for (unsigned k = 0; k < TBC_WIN_LEN; ++k) {
        const uint8_t cur = tbc_at(r, base + (int64_t)k + 1);
        const bool valid = t->power[prev] >= 2u && t->power[cur] >= 2u;
        sum[k + 1] = sum[k] + (valid ? tbc_wrap8((int)t->phase[cur] - (int)t->phase[prev]) : 0);
        cnt[k + 1] = cnt[k] + valid;
        prev = cur;
    }
    bool any = false;
    for (int k = 0; k < TBC_WIN_LEN; ++k) {
        int lo = k - TBC_BOX / 2, hi = lo + TBC_BOX;   /* steps [k-4, k+4) */
        if (lo < 0) lo = 0;
        if (hi > TBC_WIN_LEN) hi = TBC_WIN_LEN;
        const int32_t n = cnt[hi] - cnt[lo];
        ok[k] = n * 2 >= TBC_BOX;
        v[k] = (int16_t)(ok[k] ? (sum[hi] - sum[lo]) * 16 / n : 0);
        any |= ok[k];
    }
    return any;
}

static inline bool tbc_mean(const int16_t *v, const bool *ok, int a, int b, int32_t *out)
{
    int32_t s = 0, n = 0;
    for (int k = a; k < b; ++k) if (ok[k]) { s += v[k]; ++n; }
    if (n * 2 < b - a) return false;
    *out = s / n;
    return true;
}

/* Crossing of `level` in [a, b): returns the position in Q8 samples
 * relative to the window base, -1 if none. Falling (down) or rising. */
static inline int32_t tbc_cross(const int16_t *v, const bool *ok, int a, int b, int32_t level, bool down)
{
    for (int k = a; k + 1 < b; ++k) {
        if (!ok[k] || !ok[k + 1]) continue;
        int32_t x = v[k], y = v[k + 1];
        if (down ? (x >= level && y < level) : (x <= level && y > level)) {
            int32_t frac = (x - level) * 256 / (x - y);
            return k * 256 + frac + 128;   /* step k sits between samples k and k+1 */
        }
    }
    return -1;
}

typedef struct {
    bool valid;
    int64_t edge_q16, trail_q16;    /* absolute leading / trailing edge */
    int32_t porch_x16, tip_x16;
} tbc_pulse_t;

/* Measures the pulse near `pred` (absolute Q16) and checks it is a normal
 * H sync: sync-level plateau of normal width, blanking before it and again
 * right after it (equalising, broad and serration pulses all fail). */
static inline tbc_pulse_t tbc_measure(const tbc_tables_t *t, const tbc_ring_t *r, int64_t pred_q16, int gate,
                                      int32_t want_porch, int32_t want_tip)
{
    tbc_pulse_t p = {0};
    const int64_t pred = pred_q16 >> 16, base = pred - TBC_WIN_PRE;
    int16_t v[TBC_WIN_LEN];
    bool ok[TBC_WIN_LEN];
    if (base + TBC_WIN_LEN + 1 > r->complete || !tbc_steps(t, r, base, v, ok)) return p;
    const int o = TBC_WIN_PRE;
    int32_t porch, tip;
    if (!tbc_mean(v, ok, o - 40, o - 16, &porch) || !tbc_mean(v, ok, o + 50, o + 150, &tip)) return p;
    if (porch - tip < TBC_MIN_DEPTH_X16) return p;   /* sync is the low-frequency extreme */
    /* Blanking and sync tip where this camera has them: a dark picture
     * detail between bright ones is never below blanking by a sync depth. */
    const int32_t want_depth = want_porch - want_tip, tol = want_depth * 3 / 10;
    if (want_depth < TBC_MIN_DEPTH_X16 || porch < want_porch - tol || porch > want_porch + tol ||
        tip < want_tip - tol || tip > want_tip + tol) return p;
    const int32_t mid = (porch + tip) / 2, depth = porch - tip;
    int32_t e = tbc_cross(v, ok, o - gate, o + gate + 1, mid, true);
    if (e < 0) return p;
    int32_t tr = tbc_cross(v, ok, e / 256 + TBC_WIDTH_MIN - 8, e / 256 + TBC_WIDTH_MAX + 1, mid, false);
    if (tr < 0) return p;
    const int width_q8 = tr - e;
    if (width_q8 < TBC_WIDTH_MIN * 256 || width_q8 > TBC_WIDTH_MAX * 256) return p;
    /* Back at blanking 0.3-0.7 us after the trailing edge (breezeway). */
    int32_t after;
    const int ta = tr / 256 + 12, tb = ta + 16;
    if (tb > TBC_WIN_LEN || !tbc_mean(v, ok, ta, tb, &after) || after < porch - depth / 4 ||
        after > porch + depth / 4) return p;
    p.valid = true;
    p.edge_q16 = ((base << 8) + e) << 8;
    p.trail_q16 = ((base << 8) + tr) << 8;
    p.porch_x16 = porch;
    p.tip_x16 = tip;
    return p;
}

/* Mean phase step x16 over raw steps [a, a+n), or false when fewer than
 * half carry phase. */
static inline bool tbc_level(const tbc_tables_t *t, const tbc_ring_t *r, int64_t a, int n, int32_t *out)
{
    int32_t sum = 0, cnt = 0;
    uint8_t prev = tbc_at(r, a);
    for (int k = 0; k < n; ++k) {
        const uint8_t cur = tbc_at(r, a + k + 1);
        if (t->power[prev] >= 2u && t->power[cur] >= 2u) {
            sum += tbc_wrap8((int)t->phase[cur] - (int)t->phase[prev]);
            ++cnt;
        }
        prev = cur;
    }
    if (cnt * 2 < n) return false;
    *out = sum * 16 / cnt;
    return true;
}

/* The per-line measurement while locked: ~110 phase steps instead of the
 * full window. Levels on short plateaus, the leading edge (smoothed, +-gate
 * around the prediction), and the shape checks that keep the vertical
 * interval out: still at the tip at 3.75-4.15 us (equalising pulses and
 * serrations have ended) and two thirds back to blanking at 5.0-5.2 us
 * (a broad pulse is still at the tip). The trailing edge is not needed. */
static inline tbc_pulse_t tbc_measure_fast(const tbc_tables_t *t, const tbc_ring_t *r, int64_t pred_q16, int gate,
                                           int32_t want_porch, int32_t want_tip)
{
    tbc_pulse_t p = {0};
    const int64_t pred = pred_q16 >> 16;
    if (pred + 216 > r->complete) return p;
    int32_t porch, tip, still, after;
    if (!tbc_level(t, r, pred - 40, 24, &porch) || !tbc_level(t, r, pred + 50, 32, &tip)) return p;
    const int32_t want_depth = want_porch - want_tip, tol = want_depth * 3 / 10;
    if (want_depth < TBC_MIN_DEPTH_X16 || porch < want_porch - tol || porch > want_porch + tol ||
        tip < want_tip - tol || tip > want_tip + tol || porch - tip < TBC_MIN_DEPTH_X16) return p;
    const int32_t mid = (porch + tip) / 2, depth = porch - tip;
    if (!tbc_level(t, r, pred + 150, 16, &still) || still > tip + depth / 3) return p;
    if (!tbc_level(t, r, pred + 200, 8, &after) || after < tip + depth * 2 / 3) return p;
    /* Smoothed steps v[j] centred on step pred-gate-1+j (boxcar [-4, +4)). */
    enum { MAXV = 2 * TBC_GATE_ACQ + 4 };
    const int nv = 2 * gate + 3, nd = nv + TBC_BOX;
    int8_t d[MAXV + TBC_BOX];
    bool vd[MAXV + TBC_BOX];
    const int64_t d0 = pred - gate - 1 - TBC_BOX / 2;
    uint8_t prev = tbc_at(r, d0);
    for (int k = 0; k < nd; ++k) {
        const uint8_t cur = tbc_at(r, d0 + k + 1);
        vd[k] = t->power[prev] >= 2u && t->power[cur] >= 2u;
        d[k] = (int8_t)(vd[k] ? tbc_wrap8((int)t->phase[cur] - (int)t->phase[prev]) : 0);
        prev = cur;
    }
    int32_t sum = 0, cnt = 0, last = 0;
    bool have_last = false;
    for (int k = 0; k < TBC_BOX; ++k) { sum += d[k]; cnt += vd[k]; }
    for (int j = 0; j < nv; ++j) {
        if (j) { sum += d[j + TBC_BOX - 1] - d[j - 1]; cnt += vd[j + TBC_BOX - 1] - vd[j - 1]; }
        const bool ok = cnt * 2 >= TBC_BOX;
        const int32_t v = ok ? (cnt == TBC_BOX ? sum * 2 : sum * 16 / cnt) : 0;
        if (ok && have_last && last >= mid && v < mid) {
            const int32_t frac = (last - mid) * 256 / (last - v);
            /* between steps j-1 and j; a step sits between its two samples */
            const int64_t pos_q8 = ((pred - gate - 1 + j - 1) << 8) + frac + 128;
            p.valid = true;
            p.edge_q16 = pos_q8 << 8;
            p.porch_x16 = porch;
            p.tip_x16 = tip;
            return p;
        }
        last = v;
        have_last = ok;
    }
    return p;
}

/* Acquisition: the first normal-width sync run in [from, to) (smoothed
 * steps over the whole span, extremes as in afc2_measure). */
static inline bool tbc_acquire(const tbc_tables_t *t, const tbc_ring_t *r, const tbc_t *s, int64_t from, int64_t to,
                               int64_t *edge_q16)
{
    while (from + TBC_WIN_LEN + 1 <= to && from + TBC_WIN_LEN + 1 <= r->complete) {
        int16_t v[TBC_WIN_LEN];
        bool ok[TBC_WIN_LEN];
        if (tbc_steps(t, r, from, v, ok)) {
            int32_t lo = INT16_MAX, hi = INT16_MIN;
            for (int k = 0; k < TBC_WIN_LEN; ++k)
                if (ok[k]) { if (v[k] < lo) lo = v[k]; if (v[k] > hi) hi = v[k]; }
            if (hi - lo >= TBC_MIN_DEPTH_X16) {
                const int32_t th = lo + (hi - lo) * 3 / 20;
                int run = 0;
                for (int k = 0; k < TBC_WIN_LEN; ++k) {
                    if (ok[k] && v[k] <= th) {
                        if (++run == 1 && k > 24) {
                            /* candidate start: confirm by a full measurement */
                            tbc_pulse_t p = tbc_measure(t, r, (from + k) << 16, TBC_GATE_ACQ,
                                                        s->ref_porch_x16, s->ref_tip_x16);
                            if (p.valid) { *edge_q16 = p.edge_q16; return true; }
                        }
                    } else run = 0;
                }
            }
        }
        from += TBC_WIN_LEN - TBC_WIDTH_MAX - 40;
    }
    return false;
}

/* Writes the clean leading edge for an edge at `edge_q16`: from TBC_LEAD
 * samples before it (front porch, porch frequency) through the raised-cosine
 * edge into the first TBC_TIP_KEEP samples of the tip (tip frequency). The
 * segment ends inside the real tip, phase-continuous with it, so the
 * demodulator's recurrent state re-converges during the remaining tip; the
 * trailing edge, back porch and burst stay the camera's own samples (the
 * colour is never touched). Line PLLs time the leading edge. False (nothing
 * written) when the segment is not complete in the ring or would start
 * before r->write_min. */
static inline bool tbc_write(const tbc_tables_t *t, tbc_ring_t *r, const tbc_t *s, int64_t edge_q16)
{
    const int64_t a = (edge_q16 >> 16) - TBC_LEAD, b = (edge_q16 >> 16) + TBC_TIP_KEEP;
    const int len = (int)(b - a);
    if (len <= 0 || len > TBC_SEG_MAX || a - 32 < 0) return false;
    if (b + 8 > r->complete || a < r->write_min) return false;
    /* Radius of the real signal just before the segment. */
    unsigned rsum = 0;
    for (int64_t k = a - 32; k < a; ++k) rsum += t->radius_x4[tbc_at(r, k)];
    int rad = (int)((rsum + 64u) / 128u);   /* mean radius x4 / 4, rounded */
    if (rad < 2) rad = 2;
    if (rad > 7) rad = 7;
    const uint8_t *best = t->best[rad - 2];
    /* Phase in Q8 phase codes; frequencies are steps x16 -> Q8 x16. */
    const int32_t fp = s->porch_x16 * 16, fs = s->tip_x16 * 16;
    int32_t ph[TBC_SEG_MAX];
    int16_t on[TBC_SEG_MAX];
    int32_t acc = (int32_t)t->phase[tbc_at(r, a - 1)] << 8;
    int tip_n = 0;
    for (int k = 0; k < len; ++k) {
        /* Step k runs from sample a+k-1 to a+k: its centre is a+k-0.5. */
        const int64_t centre_q16 = ((a + k) << 16) - 32768;
        on[k] = (int16_t)tbc_ramp(t, centre_q16 - edge_q16);
        acc += fp + (int32_t)(((int64_t)(fs - fp) * on[k]) >> 12);
        ph[k] = acc;
        tip_n += on[k] == 4096;
    }
    if (tip_n < TBC_TIP_KEEP / 2) return false;
    /* The real tip after the segment continues at the tip frequency: aim at
     * its phase, averaged over 8 samples, and spread the difference over
     * the synthetic tip (at most half a turn: <= ~0.3 MHz on the tip level). */
    int32_t target = ((int32_t)t->phase[tbc_at(r, b)] << 8) - fs;
    {
        int32_t sum = 0, n = 0;
        for (int k = 0; k < 8; ++k) {
            const uint8_t x = tbc_at(r, b + k);
            if (t->power[x] < 2u) continue;
            int32_t d = (((int32_t)t->phase[x] << 8) - fs * (k + 1) - target) & 0xFFFF;
            if (d >= 0x8000) d -= 0x10000;
            sum += d;
            ++n;
        }
        if (n) target += sum / n;
    }
    int32_t delta = (target - ph[len - 1]) & 0xFFFF;
    if (delta >= 0x8000) delta -= 0x10000;
    const int32_t step_q16 = (int32_t)(((int64_t)delta << 16) / tip_n);
    int32_t corr_q16 = 0;
    for (int k = 0; k < len; ++k) {
        if (on[k] == 4096) corr_q16 += step_q16;
        const int32_t p = ph[k] + (corr_q16 >> 16);
        r->ring[(uint32_t)(a + k) & r->mask] = best[((p + 128) >> 8) & 255];
    }
    return true;
}

/* One line: measure near the prediction, update the PLL and rewrite a
 * verified pulse. Call in order for each predicted line whose window is
 * complete. Returns true when it advanced to the next line. */
static inline bool tbc_line(const tbc_tables_t *t, tbc_t *s, tbc_ring_t *r)
{
    const int64_t pred = s->next_q16;
    if ((pred >> 16) - TBC_WIN_PRE + TBC_WIN_LEN + 1 > r->complete) return false;
    ++s->lines;
    /* Own smoothed levels once tracking; the AFC reference again after a
     * few misses (a retune moves every level at once). */
    const bool own = s->levels && s->good >= TBC_LOCK_LINES / 2 && s->misses < 4;
    const int32_t wp = own ? s->porch_x16 : s->ref_porch_x16, wt = own ? s->tip_x16 : s->ref_tip_x16;
    /* Full window (with the trailing edge) until locked, then the fast one. */
    tbc_pulse_t p = s->locked ? tbc_measure_fast(t, r, pred, TBC_GATE_LOCKED, wp, wt)
                              : tbc_measure(t, r, pred, s->good ? TBC_GATE_LOCKED : TBC_GATE_ACQ, wp, wt);
    int64_t clean = pred;
    if (p.valid) {
        const int64_t err = p.edge_q16 - pred;
        s->last_err_q8 = (int32_t)(err >> 8);
        ++s->measured;
        s->misses = 0;
        /* Critically damped, ~50 lines: a = 0.06, b = 0.0009. */
        const int64_t gain_a = s->locked ? 61 : 245;   /* /1024 */
        s->next_q16 += err * gain_a / 1024;
        s->period_q16 += (int32_t)(err * 59 / 65536);
        /* Never let the period walk far from the standard. */
        const int32_t lim = s->nominal_q16 / 500;   /* 0.2 % */
        if (s->period_q16 > s->nominal_q16 + lim) s->period_q16 = s->nominal_q16 + lim;
        if (s->period_q16 < s->nominal_q16 - lim) s->period_q16 = s->nominal_q16 - lim;
        if (!s->levels) { s->porch_x16 = p.porch_x16; s->tip_x16 = p.tip_x16; s->levels = true; }
        else {
            s->porch_x16 += (p.porch_x16 - s->porch_x16) / 20;
            s->tip_x16 += (p.tip_x16 - s->tip_x16) / 20;
        }
        if (p.trail_q16) {
            const int32_t w_q4 = (int32_t)((p.trail_q16 - p.edge_q16) >> 12);
            s->width_q4 = s->width_q4 ? s->width_q4 + (w_q4 - s->width_q4) / 20 : w_q4;
        }
        if (++s->good >= TBC_LOCK_LINES && !s->locked) { s->locked = true; ++s->locks; }
        clean = s->next_q16;
        if (s->locked && (err < 0 ? -err : err) <= ((int64_t)TBC_REWRITE_ERR << 16)) {
            if (tbc_write(t, r, s, clean)) ++s->rewritten;
            else ++s->refused_late;
        }
    } else {
        ++s->refused_shape;
        /* Before lock, the very next line must confirm: a wrong candidate is
         * dropped at once, never followed on prediction. */
        if (!s->locked) { s->have = false; s->good = 0; s->misses = 0; return true; }
        if (++s->misses >= TBC_UNLOCK_MISSES) {
            if (s->locked) ++s->unlocks;
            s->locked = false;
            s->have = false;
            s->good = 0;
        }
    }
    s->next_q16 += s->period_q16;
    return true;
}

/* Process every line whose window is complete. Acquires first when not
 * tracking; lines that fell out of the ring (a long CPU blackout) are
 * skipped on the prediction. */
static inline void tbc_run(const tbc_tables_t *t, tbc_t *s, tbc_ring_t *r, unsigned max_lines)
{
    const int64_t ring = (int64_t)r->mask + 1;
    if (!s->have) {
        int64_t from = r->complete - 3200;   /* a bit more than one line */
        if (from < r->write_min) from = r->write_min;
        if (from < 0) from = 0;
        int64_t edge;
        if (!s->ref || !tbc_acquire(t, r, s, from, r->complete, &edge)) return;
        ++s->acquisitions;
        s->have = true;
        s->locked = false;
        s->levels = false;
        s->good = s->misses = 0;
        s->period_q16 = s->nominal_q16;
        s->next_q16 = edge;
    }
    /* The window must still hold RX data of this pass. */
    while ((s->next_q16 >> 16) - TBC_WIN_PRE < r->complete - ring + 4096) s->next_q16 += s->period_q16;
    for (unsigned k = 0; k < max_lines && s->have && tbc_line(t, s, r); ++k) {}
}
