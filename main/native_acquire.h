/* C5VRX by Twotoz/contributors: bounded native acquire/hold candidate.
 * Extends legacy native_agc_pace; settled PBUS is evidence of a candidate
 * landing, not a physically verified acquisition-complete hardware flag. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
/* Leave room for the existing 5-ms control settling guard between scans. */
#define NATIVE_PERIOD_US 20000u
#define NATIVE_TIMEOUT_US 60u
#define NATIVE_STILL_US 2u
#define NATIVE_MAX_GAP_US 2u
typedef struct {
    uint32_t last_us, stable_since;
    uint32_t gain; /* observed packed RF/BB/fine tuple; no table search while open */
    bool started, have_gain;
} native_acquire_t;
typedef enum { NATIVE_WAIT, NATIVE_LANDING, NATIVE_TIMEOUT, NATIVE_GAP } native_acquire_result_t;
static inline native_acquire_result_t native_acquire_observe(native_acquire_t *a,
    uint32_t us, bool start_seen, bool gain_valid, uint32_t gain)
{
    if (us >= NATIVE_TIMEOUT_US) return NATIVE_TIMEOUT;
    if (us < a->last_us || us - a->last_us > NATIVE_MAX_GAP_US) return NATIVE_GAP;
    a->last_us = us;
    if (start_seen) {
        a->started = true;
        a->have_gain = false;
        a->stable_since = us;
        return NATIVE_WAIT;
    }
    if (!a->started || !gain_valid) { a->have_gain = false; return NATIVE_WAIT; }
    if (!a->have_gain || a->gain != gain) {
        a->gain = gain;
        a->have_gain = true;
        a->stable_since = us;
        return NATIVE_WAIT;
    }
    return us - a->stable_since >= NATIVE_STILL_US ? NATIVE_LANDING : NATIVE_WAIT;
}
