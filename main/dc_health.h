/* C5VRX-5 by Twotoz and the C5VRX contributors: passive DC calibration
 * health in flight and the repair flags for the next boot (pure logic,
 * host test test/test_dc_health.c).
 *
 * A DC table that does not fit the live receiver costs range silently: on
 * 2026-10-19 a table of another filter left 3.7 cells of DC and cost ~10 dB
 * in research/sensitivity_model.py, with a picture that looked normal. The
 * calibration task watches for that in flight without touching the receiver
 * and, after a sustained finding, stores one flag per (frequency, filter)
 * in NVS; the menu shows DC CAL NEEDED. The next boot re-measures exactly
 * the flagged tables (on a quiet channel when the VTX is on) and clears a
 * flag only when its table is complete again. Nothing is searched in flight.
 *
 * Findings (reasons):
 *   TABLE   the live gain row flies on codes that were not measured for this
 *           channel and filter (template, missing, or another filter);
 *   FILTER  the analog filter registers do not read back as set;
 *   DRIFT   the live trim cannot hold the DC: its mean stays above
 *           DC_HEALTH_DRIFT_MCELLS on qualified windows.
 * A window without qualified evidence (menu, scan, gain settling, no DC
 * estimate) changes nothing: unknown is never reported as a failure. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

enum { DC_HEALTH_OK = 0, DC_HEALTH_TABLE = 1, DC_HEALTH_FILTER = 2, DC_HEALTH_DRIFT = 3, DC_HEALTH_REASONS };

/* Sustain before a finding latches. A channel or profile change settles in
 * well under a second (dco_select every 250 ms); the trim brings a retune's
 * 2-4 cells back below one cell within seconds (DC_TRIM_FAST_US). */
#define DC_HEALTH_TABLE_US  (20LL * 1000000LL)
#define DC_HEALTH_FILTER_US (10LL * 1000000LL)
#define DC_HEALTH_DRIFT_US  (60LL * 1000000LL)
/* 1.5 cells: the model gives 2-3 dB for a DC this size at the range edge,
 * well above the ~0.5-cell wander the trim leaves (deadband 600). */
#define DC_HEALTH_DRIFT_MCELLS 1500

static inline const char *dc_health_reason_name(unsigned r)
{
    static const char *const names[DC_HEALTH_REASONS] = {"ok", "table", "filter", "drift"};
    return r < DC_HEALTH_REASONS ? names[r] : "unknown";
}

static inline int64_t dc_health_hold_us(unsigned reason)
{
    return reason == DC_HEALTH_FILTER ? DC_HEALTH_FILTER_US :
           reason == DC_HEALTH_DRIFT ? DC_HEALTH_DRIFT_US : DC_HEALTH_TABLE_US;
}

/* One finding: bad since (0 = not bad), for the context it was seen in. */
typedef struct {
    uint16_t freq, key;
    int64_t since_us;
    bool latched;
} dc_watch_t;

/* Feed one observation of one reason. qualified=false leaves the watch as it
 * is. A context change starts over (a new table is judged on its own). Returns
 * true exactly once, when the finding latches for this context. */
static inline bool dc_watch_step(dc_watch_t *w, unsigned reason, uint16_t freq, uint16_t key, bool qualified,
                                 bool bad, int64_t now_us)
{
    if (w->freq != freq || w->key != key) *w = (dc_watch_t){freq, key, 0, false};
    if (!qualified || w->latched) return false;
    if (!bad) { w->since_us = 0; return false; }
    if (!w->since_us) { w->since_us = now_us ? now_us : 1; return false; }
    if (now_us - w->since_us < dc_health_hold_us(reason)) return false;
    w->latched = true;
    return true;
}

/* Persistent repair flags ("dc_recal" in NVS). */
#define DC_FLAGS 6u
#define DC_FLAGS_VERSION 1u
typedef struct {
    uint16_t freq, key;
    uint8_t reason, pad;
} dc_flag_t;
typedef struct {
    uint8_t version, count, pad[2];
    dc_flag_t f[DC_FLAGS];
} dc_flags_t;

static inline void dc_flags_init(dc_flags_t *x) { memset(x, 0, sizeof(*x)); x->version = DC_FLAGS_VERSION; }

static inline bool dc_flags_valid(const dc_flags_t *x)
{
    return x->version == DC_FLAGS_VERSION && x->count <= DC_FLAGS;
}

static inline int dc_flags_find(const dc_flags_t *x, uint16_t freq, uint16_t key)
{
    for (unsigned k = 0; k < x->count; ++k)
        if (x->f[k].freq == freq && x->f[k].key == key) return (int)k;
    return -1;
}

/* Add or update; returns true when the stored flags changed (then save).
 * Full: the oldest flag goes (its channel is repaired on that channel's boot
 * anyway through its template/missing rows). */
static inline bool dc_flags_add(dc_flags_t *x, uint16_t freq, uint16_t key, uint8_t reason)
{
    const int at = dc_flags_find(x, freq, key);
    if (at >= 0) {
        if (x->f[at].reason == reason) return false;
        x->f[at].reason = reason;
        return true;
    }
    if (x->count == DC_FLAGS) {
        memmove(&x->f[0], &x->f[1], (DC_FLAGS - 1u) * sizeof(x->f[0]));
        --x->count;
    }
    x->f[x->count++] = (dc_flag_t){freq, key, reason, 0};
    return true;
}

static inline bool dc_flags_clear(dc_flags_t *x, uint16_t freq, uint16_t key)
{
    const int at = dc_flags_find(x, freq, key);
    if (at < 0) return false;
    memmove(&x->f[at], &x->f[at + 1], (x->count - (unsigned)at - 1u) * sizeof(x->f[0]));
    --x->count;
    memset(&x->f[x->count], 0, sizeof(x->f[0]));
    return true;
}
