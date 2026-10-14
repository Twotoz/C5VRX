/* C5VRX by Twotoz/contributors: necessary-only sampling adjustment policy.
 * Passive observations cannot prove a phase actuator or rollback. Caller
 * supplies source/context and hardware evidence; no registers are written.
 * The existing 1-us divider pulse does NOT satisfy the phase proof contract. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "sphase_logic.h"

#define SPM_SAMPLE_MAX_AGE_US 100000u
#define SPM_CONFIRM_MAX_US 4000u
#define SPM_TRANSACTION_MAX_US 4000u
#define SPM_BACKOFF_US 60000000u
typedef struct {
    uint32_t phy, control, gain, dco, filter, transport;
} sphase_monitor_context_t;
typedef struct {
    sphase_monitor_context_t context;
    uint64_t sample_id, observed_us;
    uint32_t sampling_epoch;
    unsigned phase;
    int excess, reference_best;
    unsigned clip_pm, origin_pm, repeat_pm;
    bool valid, original, porch_verified, carrier, dc_valid, batch_stable;
    bool timing_reference_valid, reference_contrast;
} sphase_monitor_sample_t;
typedef enum { SPM_NONE, SPM_APPLY_ONE, SPM_RESTORE_ORIGINAL, SPM_KEEP, SPM_STOP } sphase_monitor_action_t;
typedef enum {
    SPM_IDLE, SPM_SUSPECT, SPM_UNOBSERVABLE, SPM_BLOCKED, SPM_APPLY_PENDING,
    SPM_VERIFYING, SPM_ROLLBACK_PENDING, SPM_BACKOFF, SPM_FAULT
} sphase_monitor_state_t;
typedef struct {
    sphase_monitor_context_t context;
    sphase_monitor_state_t state;
    uint64_t last_sample_id, strike_us, transaction_us, backoff_until;
    uint32_t sampling_epoch;
    unsigned original_phase, requested_phase, strikes, attempts, moved, rollbacks;
    int baseline;
    bool have_sample, actuator_proven, rollback_proven;
} sphase_monitor_t;

static inline bool sphase_monitor_same_context(sphase_monitor_context_t a, sphase_monitor_context_t b)
{
    return a.phy == b.phy && a.control == b.control && a.gain == b.gain &&
           a.dco == b.dco && a.filter == b.filter && a.transport == b.transport;
}
static inline void sphase_monitor_init(sphase_monitor_t *m, bool actuator_proven, bool rollback_proven)
{
    memset(m, 0, sizeof(*m));
    m->actuator_proven = actuator_proven; m->rollback_proven = rollback_proven;
}
static inline bool sphase_monitor_qualified(const sphase_monitor_sample_t *s, uint64_t now)
{
    return s->valid && s->original && s->porch_verified && s->carrier && s->dc_valid && s->batch_stable &&
           s->phase < 3u && s->excess >= 0 && s->reference_best >= 0 &&
           s->clip_pm <= 50u && s->origin_pm < 500u && s->repeat_pm < 500u &&
           s->observed_us <= now && now - s->observed_us <= SPM_SAMPLE_MAX_AGE_US;
}
static inline sphase_monitor_action_t sphase_monitor_restore(sphase_monitor_t *m)
{
    m->requested_phase = m->original_phase;
    m->state = SPM_ROLLBACK_PENDING;
    return SPM_RESTORE_ORIGINAL;
}

/* Two ORIGINAL stable porch observations and same-context contrasting timing
 * reference prove the excursion; a carrier's first appearance alone does not
 * authorize any scan. Executor holds analog/raw ownership throughout the
 * bounded transaction, and calls actuator_done after verified hardware action. */
static inline sphase_monitor_action_t sphase_monitor_observe(sphase_monitor_t *m,
    const sphase_monitor_sample_t *s, uint64_t now)
{
    if (m->state == SPM_FAULT || m->state == SPM_ROLLBACK_PENDING) return SPM_NONE;
    bool active = m->state == SPM_APPLY_PENDING || m->state == SPM_VERIFYING;
    if (active && (!sphase_monitor_same_context(m->context, s->context) ||
                   now < m->transaction_us)) {
        /* Restoring an old phase into a restarted/new context is unsafe. */
        m->state = SPM_FAULT;
        return SPM_STOP;
    }
    if (active && now - m->transaction_us > SPM_TRANSACTION_MAX_US)
        return sphase_monitor_restore(m);
    if (m->state == SPM_APPLY_PENDING) return SPM_NONE;
    if (m->have_sample && s->sample_id == m->last_sample_id) return SPM_NONE;
    m->last_sample_id = s->sample_id; m->have_sample = true;
    if (m->state == SPM_VERIFYING) {
        if (!sphase_monitor_qualified(s, now) || s->sampling_epoch != m->sampling_epoch ||
            s->phase != m->requested_phase) return sphase_monitor_restore(m);
        int margin = sphase_carrier_margin(s->excess);
        if (s->excess >= SPHASE_LIVE_LIMIT || (int64_t)m->baseline - s->excess <= 2LL * margin)
            return sphase_monitor_restore(m);
        ++m->moved; m->strikes = 0;
        m->state = SPM_BACKOFF;
        m->backoff_until = now > UINT64_MAX - SPM_BACKOFF_US ? UINT64_MAX : now + SPM_BACKOFF_US;
        return SPM_KEEP;
    }
    if (m->state == SPM_BACKOFF && now < m->backoff_until) return SPM_NONE;
    if (!sphase_monitor_qualified(s, now)) {
        m->strikes = 0; m->state = SPM_UNOBSERVABLE;
        return SPM_NONE;
    }
    if (s->excess < SPHASE_LIVE_LIMIT) { m->strikes = 0; m->state = SPM_IDLE; return SPM_NONE; }
    if (!s->timing_reference_valid || !s->reference_contrast ||
        (int64_t)s->excess - s->reference_best <= 2LL * sphase_carrier_margin(s->reference_best)) {
        m->strikes = 0; m->state = SPM_UNOBSERVABLE;
        return SPM_NONE;
    }
    if (!m->strikes || !sphase_monitor_same_context(m->context, s->context) ||
        m->sampling_epoch != s->sampling_epoch || m->original_phase != s->phase ||
        now < m->strike_us || now - m->strike_us > SPM_CONFIRM_MAX_US) {
        m->context = s->context; m->sampling_epoch = s->sampling_epoch;
        m->original_phase = s->phase; m->baseline = s->excess;
        m->strike_us = now; m->strikes = 1; m->state = SPM_SUSPECT;
        return SPM_NONE;
    }
    if (s->excess < m->baseline) m->baseline = s->excess;
    m->strikes = 2;
    /* Reserve one epoch for the candidate and one for a possible rollback. */
    if (!m->actuator_proven || !m->rollback_proven || m->sampling_epoch >= UINT32_MAX - 1u) {
        m->state = SPM_BLOCKED; return SPM_NONE;
    }
    m->requested_phase = (m->original_phase + 1u) % 3u;
    m->transaction_us = now; m->state = SPM_APPLY_PENDING; ++m->attempts;
    return SPM_APPLY_ONE;
}

/* Hardware proof includes exact requested phase, restored 40MHz divider,
 * freshly bounded producer/consumer lag and a new sampling epoch. Returning
 * the divider register to6 alone does not prove any of those phase/lag facts. */
static inline sphase_monitor_action_t sphase_monitor_actuator_done(sphase_monitor_t *m,
    bool phase_verified, bool rate_restored, bool lag_rebounded, uint32_t epoch, uint64_t now)
{
    bool proof = phase_verified && rate_restored && lag_rebounded && epoch > m->sampling_epoch &&
                 now >= m->transaction_us && now - m->transaction_us <= SPM_TRANSACTION_MAX_US;
    if (m->state == SPM_APPLY_PENDING) {
        if (!proof) {
            /* A failed phase/rate proof can still have advanced hardware.
             * Its epoch cannot subsequently masquerade as a fresh rollback. */
            if (epoch > m->sampling_epoch) m->sampling_epoch = epoch;
            return sphase_monitor_restore(m);
        }
        m->sampling_epoch = epoch; m->state = SPM_VERIFYING;
        return SPM_NONE;
    }
    if (m->state != SPM_ROLLBACK_PENDING) return SPM_NONE;
    if (!phase_verified || !rate_restored || !lag_rebounded || epoch <= m->sampling_epoch) {
        m->state = SPM_FAULT; return SPM_STOP;
    }
    ++m->rollbacks; m->sampling_epoch = epoch; m->strikes = 0; m->state = SPM_BACKOFF;
    m->backoff_until = now > UINT64_MAX - SPM_BACKOFF_US ? UINT64_MAX : now + SPM_BACKOFF_US;
    return SPM_NONE;
}
