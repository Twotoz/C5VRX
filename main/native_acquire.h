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
/* Pinned PHY RX-command BB field is nine bits: low seven are gain, bit
 * eight is channel-mode context; bit seven must be zero. Check the expected
 * context before stripping it. The current live bench uses prefix 0x100;
 * this helper does not establish support for other RF/BW contexts. */
static inline bool native_acquire_pack_pbus(uint16_t rf, uint16_t bb,
    uint16_t fine, uint16_t expected_bb_prefix, uint32_t *word)
{
    if (!word || (expected_bb_prefix != 0 && expected_bb_prefix != 0x100u) ||
        rf > 511u || (bb & ~127u) != expected_bb_prefix ||
        (fine & ~7u) != 0xd0u) return false;
    *word = ((uint32_t)rf << 12) | ((uint32_t)(bb & 127u) << 4) | (fine & 7u);
    return true;
}
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
