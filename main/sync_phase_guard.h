/* C5VRX by Twotoz/contributors: camera-phase guard for the live flywheel.
 * Pure task-side logic on the comparator's native pulses (20 MHz ticks).
 *
 * The ISR suppresses a repair only when a native edge lands within its
 * +-4 us window. A camera that is present but off the held grid (VTX
 * power cycle, long static, a model the probe could not requalify, or
 * slow period drift while no update arrives) then received a second,
 * competing sync: the monitor rolled horizontally and showed the top of
 * the picture at the bottom. This guard compares every native H/broad
 * pulse with the running grid and
 *  - yields (no OE clamp) while a coherent off-grid camera is present,
 *  - re-anchors H by the coherent offset, and V by whole halflines,
 *  - trims small coherent H offsets so drift never reaches the window.
 * Random noise pulses are incoherent and cannot build a score. Evidence
 * is timing only; it does not prove analogue levels or monitor lock. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

#define SPG_WINDOW 80          /* ticks: the ISR's native suppression window */
#define SPG_SCORE_MAX 32u
#define SPG_LOCK 12u           /* coherent H pulses before yield/re-anchor */
#define SPG_TRIM_N 32u         /* aligned pulses averaged per fine trim */
#define SPG_TRIM_MIN 8         /* ticks: smaller mean offsets are left alone */
#define SPG_V_LOCK 2u          /* consecutive fields with the same V offset */
#define SPG_HOLD_TICKS 2000000u /* 100 ms without camera evidence ends a yield */
#define SPG_V_SPACING 128      /* ticks: V evidence must repeat on whole fields */
#define SPG_V_FIELDS 4u        /* ...at most this many fields apart */
/* Evidence ages. In-phase H confidence older than one field restarts from
 * zero; acquisition needs H within ~10 lines (the V interval carries no H
 * for up to ~9 lines) and V within two fields of the deciding pulse. */
#define SPG_H_STALE_TICKS 400000u
#define SPG_H_FRESH_TICKS 12800u
#define SPG_V_FRESH_TICKS 800000u

typedef enum { SPG_NONE = 0, SPG_JUMP_H, SPG_JUMP_V } spg_kind_t;
typedef struct { spg_kind_t kind; int32_t value; } spg_action_t;

typedef struct {
    int32_t h_offset, trim_sum, v_offset;
    uint8_t h_score, aligned, v_score, v_good;
    uint16_t trim_n;
    uint64_t last_evidence, v_tick, h_tick;
    bool yield_h, yield_v, have_v, have_h;
} spg_t;

static inline void spg_reset(spg_t *g)
{
    bool yh = g->yield_h, yv = g->yield_v;
    uint64_t last = g->last_evidence;
    *g = (spg_t){0};
    /* A re-anchor keeps yielding until in-phase evidence confirms it. */
    g->yield_h = yh; g->yield_v = yv; g->last_evidence = last;
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

/* offset: native H rise minus the nearest grid H line start (ticks). */
static inline spg_action_t spg_h(spg_t *g, int32_t offset, int32_t line, uint64_t tick)
{
    spg_action_t action = {SPG_NONE, 0};
    if (spg_abs(offset) <= SPG_WINDOW) {
        /* Confidence from a camera seen long ago is not current evidence. */
        if (!g->have_h || tick < g->h_tick || tick - g->h_tick > SPG_H_STALE_TICKS) {
            g->aligned = 0; g->trim_sum = 0; g->trim_n = 0;
        }
        g->h_tick = tick; g->have_h = true;
        if (g->aligned < SPG_SCORE_MAX) ++g->aligned;
        if (g->h_score) --g->h_score;
        g->last_evidence = tick;
        if (g->aligned >= SPG_LOCK) g->yield_h = false;
        g->trim_sum += offset;
        if (++g->trim_n >= SPG_TRIM_N) {
            int32_t mean = g->trim_sum / (int32_t)g->trim_n;
            g->trim_sum = 0; g->trim_n = 0;
            if (spg_abs(mean) >= SPG_TRIM_MIN && g->aligned >= SPG_LOCK)
                action = (spg_action_t){SPG_JUMP_H, mean};
        }
        return action;
    }
    if (g->aligned) --g->aligned;
    int32_t d = spg_wrap((int64_t)offset - g->h_offset, line);
    if (g->h_score && spg_abs(d) <= SPG_WINDOW) {
        if (g->h_score < SPG_SCORE_MAX) ++g->h_score;
        g->h_offset = spg_wrap((int64_t)g->h_offset + d / 4, line);
        g->last_evidence = tick;
    } else if (g->h_score) {
        --g->h_score;
    } else {
        g->h_offset = offset; g->h_score = 1;
    }
    if (g->h_score >= SPG_LOCK) {
        g->yield_h = true;
        action = (spg_action_t){SPG_JUMP_H, g->h_offset};
    }
    return action;
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
 * coherent camera H (aligned) and the camera's V on two field-spaced
 * observations at the grid's own broad slot. Independent of the strict
 * EQ/broad/EQ grammar, which board captures often failed at range. */
static inline bool spg_acquired(const spg_t *g, uint64_t now)
{
    return !g->yield_h && !g->yield_v && g->aligned >= SPG_LOCK && g->v_good >= SPG_V_LOCK &&
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
