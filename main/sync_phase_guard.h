/* C5VRX by Twotoz/contributors: camera-phase guard for the live flywheel.
 * Pure task-side logic on the comparator's native pulses (20 MHz ticks).
 *
 * The ISR suppresses a repair only when a native edge lands within its
 * +-4 us window. A camera that is present but off the held grid (VTX
 * power cycle, long static, slow period drift) then received a second,
 * competing sync: the monitor rolled horizontally and showed the top of
 * the picture at the bottom. This guard compares native H pulses with the
 * running grid and
 *  - yields (no OE clamp) while a coherent off-grid camera is present,
 *  - re-anchors H by the camera's offset, and V by whole halflines,
 *  - trims small coherent H offsets so drift never reaches the window,
 *  - reports a camera running the other standard (PAL/NTSC line period).
 *
 * Evidence is counted per line, not per pulse (2026-10-18): a per-pulse
 * up/down score was drained by noise, and from ~1 noise pulse per line
 * (range edge) the guard lost a present camera and kept re-anchoring.
 * A phase histogram (SPG_BINS per line, hit or no hit per line, running
 * mean over ~32 lines) compares the best phase with the measured noise
 * level of all other phases: a camera is the phase hit clearly more often
 * than chance, however dense the noise. A second histogram on the other
 * standard's line period sees a camera this grid cannot hold.
 * Evidence is timing only; it does not prove analogue levels or monitor lock. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

#define SPG_WINDOW 80          /* ticks: the ISR's native suppression window */
#define SPG_FINE 40            /* ticks around the camera phase: its own pulses */
#define SPG_SCORE_MAX 32u
#define SPG_LOCK 12u           /* consecutive lines of camera evidence before acting */
#define SPG_TRIM_N 32u         /* camera pulses averaged per fine trim */
#define SPG_TRIM_MIN 8         /* ticks: smaller mean offsets are left alone */
#define SPG_TRIM_SEM 4         /* ...and means within 4 standard errors (static near the camera) */
#define SPG_V_LOCK 2u          /* consecutive fields with the same V offset */
#define SPG_HOLD_TICKS 2000000u /* 100 ms without camera evidence ends a yield */
#define SPG_V_SPACING 128      /* ticks: V evidence must repeat on whole fields */
#define SPG_V_FIELDS 4u        /* ...at most this many fields apart */
#define SPG_BINS 32u           /* phase bins per line (~40 ticks); a pair spans the window */
#define SPG_RATE 4096          /* histogram unit: hit on every line */
#define SPG_EMA 6              /* running mean over 2^6 lines (~4 ms) */
#define SPG_GAP_LINES 96u      /* longer without any pulse: evidence is gone */
/* Camera = best bin pair minus two noise bins >= 25 % of lines and >= 7
 * standard deviations of that difference under the measured noise rate
 * (dense static has proportionally larger chance peaks); a lock is kept
 * down to 2/3 of the threshold. */
#define SPG_CONTRAST_MIN 1024
#define SPG_SIGMAS 7u
/* Lines of evidence a camera needs from nothing: histogram rise plus lock. */
#define SPG_ACQ_LINES 160u
/* Evidence ages: acquisition needs camera H within ~10 lines (the V
 * interval carries no H for up to ~9 lines) and V within two fields. */
#define SPG_H_FRESH_TICKS 12800u
#define SPG_V_FRESH_TICKS 800000u

typedef enum { SPG_NONE = 0, SPG_JUMP_H, SPG_JUMP_V, SPG_STD } spg_kind_t;
typedef struct { spg_kind_t kind; int32_t value; } spg_action_t;

typedef struct {
    uint16_t bin[SPG_BINS], alt[SPG_BINS];
    uint32_t mask, alt_mask;      /* bins hit on the line being collected */
    uint64_t line_no, alt_ref;
    int32_t cam, trim_sum, v_offset;
    uint32_t trim_sq;
    uint8_t aligned, h_score, alt_score, v_score, v_good;
    uint16_t trim_n;
    uint64_t last_evidence, v_tick, h_tick;
    bool started, locked, alt_locked, yield_h, yield_v, have_v, have_h;
} spg_t;

static inline void spg_reset(spg_t *g)
{
    bool yh = g->yield_h, yv = g->yield_v;
    uint64_t last = g->last_evidence;
    *g = (spg_t){0};
    /* A re-anchor keeps yielding until in-phase evidence confirms it. */
    g->yield_h = yh; g->yield_v = yv; g->last_evidence = last;
}

/* After a V re-anchor: V evidence restarts, H lock is kept. */
static inline void spg_reset_v(spg_t *g)
{
    g->v_offset = 0; g->v_score = 0; g->v_good = 0; g->v_tick = 0; g->have_v = false;
}

static inline int32_t spg_abs(int32_t v) { return v < 0 ? -v : v; }

/* Signed distance on a circle of `period` units, in (-period/2, period/2]. */
static inline int32_t spg_wrap(int64_t value, int64_t period)
{
    int64_t r = value % period;
    if (r < 0) r += period;
    if (r > period / 2) r -= period;
    return (int32_t)r;
}

static inline uint32_t spg_bin(int32_t offset, int32_t line)
{
    int64_t b = ((int64_t)offset + line / 2) * (int64_t)SPG_BINS / line;
    return b < 0 ? 0u : b >= (int64_t)SPG_BINS ? SPG_BINS - 1u : (uint32_t)b;
}

/* Close `lines` collected lines: the first with `mask`, the rest empty. */
static inline void spg_age(uint16_t *bin, uint32_t mask, uint64_t lines)
{
    if (lines >= SPG_GAP_LINES) { for (uint32_t b = 0; b < SPG_BINS; ++b) bin[b] = 0; return; }
    for (uint32_t b = 0; b < SPG_BINS; ++b) {
        int32_t v = bin[b];
        v += (((mask >> b) & 1u ? SPG_RATE : 0) - v) >> SPG_EMA;
        for (uint64_t k = 1; k < lines; ++k) v -= v >> SPG_EMA;
        bin[b] = (uint16_t)v;
    }
}

static inline uint32_t spg_isqrt(uint32_t v)
{
    uint32_t r = 0, bit = 1u << 30;
    while (bit > v) bit >>= 2;
    while (bit) {
        if (v >= r + bit) { v -= r + bit; r = (r >> 1) + bit; } else r >>= 1;
        bit >>= 2;
    }
    return r;
}

/* Best adjacent bin pair; returns its contrast against two mean bins and
 * the lock threshold for the measured noise rate. */
static inline int32_t spg_best(const uint16_t *bin, uint32_t *best, int32_t *threshold)
{
    uint32_t total = 0, top = 0, at = 0;
    for (uint32_t b = 0; b < SPG_BINS; ++b) {
        uint32_t pair = (uint32_t)bin[b] + bin[(b + 1u) % SPG_BINS];
        total += bin[b];
        if (pair > top) { top = pair; at = b; }
    }
    int32_t noise = (int32_t)((total - top) / (SPG_BINS - 2u));
    if (noise > SPG_RATE) noise = SPG_RATE;
    /* Running mean of a hit/no-hit line: variance p(1-p)/(2^(EMA+1)-1);
     * the pair difference against 2 noise bins has twice that. */
    uint32_t var = (uint32_t)noise * (uint32_t)(SPG_RATE - noise) / ((2u << SPG_EMA) - 1u);
    int32_t sigma = (int32_t)spg_isqrt(2u * var);
    int32_t t = (int32_t)SPG_SIGMAS * sigma;
    *threshold = t > SPG_CONTRAST_MIN ? t : SPG_CONTRAST_MIN;
    *best = at;
    return (int32_t)top - 2 * noise;
}

static inline bool spg_hyst(bool on, int32_t contrast, int32_t threshold)
{
    return contrast >= (on ? threshold - threshold / 3 : threshold);
}

/* offset: native H rise minus the nearest grid H line start (ticks);
 * line: grid line (ticks); alt_line: the other standard's line, 0 = none. */
static inline spg_action_t spg_h(spg_t *g, int32_t offset, int32_t line, int32_t alt_line, uint64_t tick)
{
    spg_action_t action = {SPG_NONE, 0};
    if (line < (int32_t)SPG_BINS) return action;
    /* Line number from the grid's own H start, so a camera near a line
     * boundary is not counted twice on one line and not at all on the next. */
    uint64_t n = (tick - (uint64_t)(int64_t)offset + (uint64_t)(line / 2)) / (uint64_t)line;
    if (!g->started) { g->started = true; g->line_no = n; g->alt_ref = tick; }
    if (n < g->line_no) return action; /* late pulse of a closed line: not evidence */
    if (n != g->line_no) {
        uint64_t lines = n - g->line_no;
        spg_age(g->bin, g->mask, lines);
        spg_age(g->alt, g->alt_mask, lines);
        g->mask = g->alt_mask = 0; g->line_no = n;
        uint32_t b, ab;
        int32_t t, at = 0, c = spg_best(g->bin, &b, &t), ac = alt_line ? spg_best(g->alt, &ab, &at) : 0;
        g->locked = spg_hyst(g->locked, c, t);
        g->alt_locked = alt_line && spg_hyst(g->alt_locked, ac, at) && ac > c;
        if (g->locked) {
            int32_t centre = (int32_t)(((int64_t)(b + 1u) * line) / (int32_t)SPG_BINS) - line / 2;
            if (spg_abs(spg_wrap((int64_t)centre - g->cam, line)) > SPG_FINE) g->cam = centre;
            g->last_evidence = tick;
            g->alt_score = 0;
            /* Off grid only when the histogram agrees with the fine phase. */
            if (spg_abs(g->cam) <= SPG_WINDOW || spg_abs(centre) <= SPG_WINDOW) {
                g->h_score = 0;
                if (g->aligned < SPG_SCORE_MAX) ++g->aligned;
                if (g->aligned >= SPG_LOCK) g->yield_h = false;
            } else {
                g->aligned = 0; g->trim_sum = 0; g->trim_sq = 0; g->trim_n = 0;
                if (g->h_score < SPG_SCORE_MAX) ++g->h_score;
                if (g->h_score >= SPG_LOCK) {
                    g->yield_h = true;
                    action = (spg_action_t){SPG_JUMP_H, g->cam};
                }
            }
        } else {
            g->aligned = 0; g->h_score = 0; g->trim_sum = 0; g->trim_sq = 0; g->trim_n = 0;
            if (g->alt_locked) {
                g->last_evidence = tick;
                if (g->alt_score < SPG_SCORE_MAX) ++g->alt_score;
                if (g->alt_score >= SPG_LOCK) action = (spg_action_t){SPG_STD, 0};
            } else g->alt_score = 0;
        }
        if (action.kind != SPG_NONE) return action;
    }
    g->mask |= 1u << spg_bin(offset, line);
    if (alt_line > (int32_t)SPG_BINS)
        g->alt_mask |= 1u << spg_bin(spg_wrap((int64_t)(tick - g->alt_ref), alt_line), alt_line);
    /* Pulses near the camera phase: a median tracker (a fixed step toward
     * each pulse, 4 ticks when far, 1 close) follows the camera; static,
     * uniform around it, pushes both ways and cannot pull it aside. */
    int32_t d = spg_wrap((int64_t)offset - g->cam, line);
    if (!g->locked || spg_abs(d) > SPG_FINE) return action;
    int32_t step = spg_abs(d) > 2 * SPG_TRIM_MIN ? 4 : 1;
    g->cam = spg_wrap((int64_t)g->cam + (d > 0 ? step : d < 0 ? -step : 0), line);
    if (g->aligned < SPG_LOCK) return action;
    g->h_tick = tick; g->have_h = true;
    g->trim_sum += offset;
    g->trim_sq += (uint32_t)(offset * offset); /* |offset| <= ~120: no overflow in 32 */
    if (++g->trim_n >= SPG_TRIM_N) {
        int64_t n = g->trim_n, sum = g->trim_sum;
        int32_t mean = (int32_t)(sum / n);
        /* |mean| >= SEM_K standard errors, exactly: mean^2 n >= K^2 var with
         * var = (n sq - sum^2) / (n (n-1)); both sides times n^2 (n-1). */
        int64_t lhs = sum * sum * (n - 1), rhs = (int64_t)SPG_TRIM_SEM * SPG_TRIM_SEM *
                      ((int64_t)g->trim_sq * n - sum * sum);
        g->trim_sum = 0; g->trim_sq = 0; g->trim_n = 0;
        if (spg_abs(mean) >= SPG_TRIM_MIN && lhs >= rhs)
            action = (spg_action_t){SPG_JUMP_H, mean};
    }
    return action;
}

/* The grid moved by `ticks` (a trim): the camera phase moves the other way;
 * the histogram stays (it is ~2 bins wide around a few-tick shift). */
static inline void spg_shift(spg_t *g, int32_t ticks)
{
    g->cam -= ticks; g->trim_sum = 0; g->trim_sq = 0; g->trim_n = 0;
}

/* Camera H currently held in phase on this grid. */
static inline bool spg_in_phase(const spg_t *g)
{
    return g->locked && !g->yield_h && g->aligned >= SPG_LOCK;
}

/* Real V repeats on whole fields (1..SPG_V_FIELDS); two noise trains at the
 * same wrong slot almost never do. */
static inline bool spg_field_spaced(uint64_t a, uint64_t b, uint64_t field)
{
    if (!field || b <= a) return false;
    uint64_t d = b - a, n = (d + field / 2u) / field;
    if (n < 1u || n > SPG_V_FIELDS) return false;
    uint64_t e = d > n * field ? d - n * field : n * field - d;
    return e <= SPG_V_SPACING;
}

/* offset: first broad pulse of a train minus the grid's first broad slot,
 * in whole halflines. Only meaningful while H is aligned. */
static inline spg_action_t spg_v(spg_t *g, int32_t offset, uint64_t tick, uint64_t field_ticks)
{
    spg_action_t action = {SPG_NONE, 0};
    if (g->yield_h) return action;
    if (!offset) {
        if (g->v_good && !spg_field_spaced(g->v_tick, tick, field_ticks)) g->v_good = 0;
        if (g->v_good < 8u) ++g->v_good;
        g->v_tick = tick;
        g->v_score = 0; g->have_v = false;
        g->last_evidence = tick;
        if (g->v_good >= SPG_V_LOCK) g->yield_v = false;
        return action;
    }
    g->v_good = 0;
    if (g->have_v && offset == g->v_offset && spg_field_spaced(g->v_tick, tick, field_ticks)) {
        if (g->v_score < 8u) ++g->v_score;
        g->last_evidence = tick;
    } else {
        g->have_v = true; g->v_offset = offset; g->v_score = 1;
    }
    g->v_tick = tick;
    if (g->v_score >= SPG_V_LOCK) {
        g->yield_v = true;
        action = (spg_action_t){SPG_JUMP_V, g->v_offset};
    }
    return action;
}

/* Camera acquisition without the probe's model: the grid already carries
 * coherent camera H in phase and the camera's V on two field-spaced
 * observations at the grid's own broad slot. */
static inline bool spg_acquired(const spg_t *g, uint64_t now)
{
    return spg_in_phase(g) && !g->yield_v && g->v_good >= SPG_V_LOCK &&
           g->have_h && now >= g->h_tick && now - g->h_tick <= SPG_H_FRESH_TICKS &&
           now >= g->v_tick && now - g->v_tick <= SPG_V_FRESH_TICKS;
}

/* Camera gone while yielding: resume holdover on the re-anchored grid. */
static inline bool spg_yield(spg_t *g, uint64_t now)
{
    if ((g->yield_h || g->yield_v) && now > g->last_evidence &&
        now - g->last_evidence > SPG_HOLD_TICKS)
        g->yield_h = g->yield_v = false;
    return g->yield_h || g->yield_v;
}
