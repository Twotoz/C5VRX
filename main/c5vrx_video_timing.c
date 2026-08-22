/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_video_timing.h"

#include <string.h>

#define DEFAULT_MIN_LINE 900u
#define DEFAULT_MAX_LINE 1700u
#define DEFAULT_STANDARD_LOCK_FRAMES 3u
#define DEFAULT_POLARITY_VOTES 3u

/* Nominal composite geometry as fractions of one line period. */
#define H_NUM 47u   /* 4.7 us sync per 64 us PAL line. */
#define H_DEN 640u
#define BROAD_NUM 273u /* 27.3 us serration/broad pulse. */
#define BROAD_DEN 640u

#define PULSE_END_GUARD_SAMPLES 3u
#define MIN_PRE_EQ_PULSES 3u
#define MAX_PRE_EQ_PULSES 9u
#define MIN_SERRATIONS 3u
#define MAX_SERRATIONS 9u
#define MIN_POST_EQ_PULSES 3u
#define MAX_POST_EQ_PULSES 9u
#define HSYNC_TIMEOUT_NUM 26u /* 3.25 lines without H loses lock. */
#define HSYNC_TIMEOUT_DEN 8u
#define H_LOCK_SCORE 3u

static void engine_reset(c5vrx_polarity_engine_t *engine)
{
    memset(engine, 0, sizeof(*engine));
}

static uint32_t clamp_period(const c5vrx_video_timing_state_t *t,
                             uint32_t value)
{
    if (value < t->cfg.minimum_line_samples) return t->cfg.minimum_line_samples;
    if (value > t->cfg.maximum_line_samples) return t->cfg.maximum_line_samples;
    return value;
}

static uint32_t h_nominal(const c5vrx_video_timing_state_t *t)
{
    return (uint32_t)(((uint64_t)t->line_period_samples * H_NUM + H_DEN / 2u) /
                      H_DEN);
}

static uint32_t broad_nominal(const c5vrx_video_timing_state_t *t)
{
    return (uint32_t)(((uint64_t)t->line_period_samples * BROAD_NUM +
                       BROAD_DEN / 2u) / BROAD_DEN);
}

static bool width_is_hsync(const c5vrx_video_timing_state_t *t,
                           uint32_t width)
{
    const uint32_t nominal = h_nominal(t);
    return width >= nominal - nominal * 2u / 5u &&
           width <= nominal + nominal / 2u;
}

static bool width_is_equalizing(const c5vrx_video_timing_state_t *t,
                                uint32_t width)
{
    const uint32_t nominal = h_nominal(t);
    return width >= nominal * 3u / 10u && width <= nominal * 7u / 10u;
}

static bool width_is_broad(const c5vrx_video_timing_state_t *t,
                           uint32_t width)
{
    const uint32_t nominal = broad_nominal(t);
    return width >= nominal * 4u / 5u && width <= nominal * 6u / 5u;
}

static void envelope_update(c5vrx_video_timing_state_t *t, uint8_t sample)
{
    if (sample < t->sync_floor) t->sync_floor = sample;
    if (sample > t->signal_peak) t->signal_peak = sample;
    if ((t->samples_seen & 1023u) == 0u) {
        if (t->sync_floor < 8u) ++t->sync_floor;
        if (t->signal_peak > 20u) --t->signal_peak;
    }
}

static uint8_t threshold_low(const c5vrx_video_timing_state_t *t)
{
    const unsigned span = t->signal_peak > t->sync_floor ?
        (unsigned)t->signal_peak - (unsigned)t->sync_floor : 8u;
    unsigned threshold = t->sync_floor + span / 4u;
    if (threshold < 4u) threshold = 4u;
    if (threshold > 14u) threshold = 14u;
    return (uint8_t)threshold;
}

static uint8_t threshold_high(const c5vrx_video_timing_state_t *t)
{
    const unsigned span = t->signal_peak > t->sync_floor ?
        (unsigned)t->signal_peak - (unsigned)t->sync_floor : 8u;
    unsigned threshold = t->signal_peak - span / 4u;
    if (threshold > 60u) threshold = 60u;
    if (threshold < 50u) threshold = 50u;
    return (uint8_t)threshold;
}

static void lose_locks(c5vrx_video_timing_state_t *t, bool *lock_lost)
{
    if (t->h_locked || t->v_locked) {
        ++t->lock_losses;
        if (lock_lost) *lock_lost = true;
    }
    t->h_locked = false;
    t->v_locked = false;
    t->vs_phase = VS_PHASE_ACTIVE;
    t->in_vertical_interval = false;
}

static void engine_note_interval(c5vrx_polarity_engine_t *engine,
                                 c5vrx_video_timing_state_t *t,
                                 uint64_t start,
                                 bool *plausible)
{
    *plausible = false;
    if (engine->have_interval) {
        const uint64_t interval = start - engine->last_hsync_start;
        if (interval >= t->cfg.minimum_line_samples &&
            interval <= t->cfg.maximum_line_samples) {
            const uint32_t error =
                (uint32_t)(interval > (uint64_t)t->line_period_samples
                               ? interval - t->line_period_samples
                               : (uint64_t)t->line_period_samples - interval);
            t->line_period_samples = clamp_period(
                t, (uint32_t)((t->line_period_samples * 7u +
                               (uint32_t)interval + 4u) / 8u));
            t->line_period_error_samples =
                (t->line_period_error_samples * 7 + (int32_t)error + 2) / 8;
            if (engine->interval_score < 8u) ++engine->interval_score;
            *plausible = true;
        } else if (engine->interval_score) {
            --engine->interval_score;
        }
    } else {
        engine->have_interval = true;
    }
    engine->last_hsync_start = start;
}

static void cast_polarity_vote(c5vrx_video_timing_state_t *t,
                               c5vrx_composite_polarity_t which,
                               bool plausible)
{
    if (!plausible) return;
    if (which == C5VRX_COMPOSITE_POLARITY_NEGATIVE) {
        ++t->polarity_votes_negative;
    } else {
        ++t->polarity_votes_positive;
    }
    if (t->polarity != C5VRX_COMPOSITE_POLARITY_UNKNOWN) return;
    const unsigned need = t->cfg.polarity_lock_votes ?
        t->cfg.polarity_lock_votes : DEFAULT_POLARITY_VOTES;
    const unsigned negative = (unsigned)t->polarity_votes_negative;
    const unsigned positive = (unsigned)t->polarity_votes_positive;
    if (negative >= need && negative > positive + 1u) {
        t->polarity = C5VRX_COMPOSITE_POLARITY_NEGATIVE;
        ++t->polarity_relock_events;
    } else if (positive >= need && positive > negative + 1u) {
        t->polarity = C5VRX_COMPOSITE_POLARITY_POSITIVE;
        ++t->polarity_relock_events;
    }
}

static void classify_standard_from_duration(c5vrx_video_timing_state_t *t,
                                            uint32_t duration_samples)
{
    /* Doubled field duration in measured-line units: 625 = PAL,
     * 525 = NTSC. Rounded division keeps integer math exact. */
    const uint32_t line = t->line_period_samples;
    const uint32_t doubled =
        (uint32_t)(((uint64_t)duration_samples * 2u + line / 2u) / line);
    c5vrx_video_standard_t evidence = C5VRX_VIDEO_STANDARD_UNKNOWN;
    if (doubled + 2u >= 625u && doubled <= 627u)
        evidence = C5VRX_VIDEO_STANDARD_PAL;
    else if (doubled + 2u >= 525u && doubled <= 527u)
        evidence = C5VRX_VIDEO_STANDARD_NTSC;
    if (evidence == C5VRX_VIDEO_STANDARD_UNKNOWN ||
        evidence != t->standard_candidate) {
        t->standard_candidate = evidence;
        t->standard_agreeing_frames = evidence ? 1u : 0u;
        return;
    }
    ++t->standard_agreeing_frames;
    const unsigned need = t->cfg.standard_lock_frames ?
        t->cfg.standard_lock_frames : DEFAULT_STANDARD_LOCK_FRAMES;
    if (t->standard != evidence && t->standard_agreeing_frames >= need) {
        t->standard = evidence;
    }
}

static void complete_vertical_interval(c5vrx_video_timing_state_t *t,
                                       c5vrx_video_timing_event_t *event,
                                       bool *emitted)
{
    t->vs_phase = VS_PHASE_ACTIVE;
    t->in_vertical_interval = false;

    const bool first_field = t->last_vs_start_sample == 0u;
    uint32_t duration = 0u;
    if (!first_field) {
        const uint64_t duration64 =
            t->current_vs_start_sample - t->last_vs_start_sample;
        if (duration64 < (uint64_t)t->cfg.minimum_line_samples * 250u ||
            duration64 > (uint64_t)t->cfg.maximum_line_samples * 340u) {
            ++t->false_vertical_candidates;
            return;
        }
        /* Interlace signature: consecutive field starts must alternate
         * between full-line and half-line grid phase. Two identical
         * classes in a row is a parity slip, corrected from the observed
         * cadence itself - never from brightness or arrival order. */
        if (t->vs_phase_class >= 0 &&
            t->vs_phase_class == t->last_vs_phase_class) {
            ++t->parity_slips;
            t->parity ^= 1u;
        }
        duration = (uint32_t)duration64;
        t->last_field_duration_samples = duration;
        classify_standard_from_duration(t, duration);
    }
    t->last_vs_phase_class = t->vs_phase_class;
    t->last_vs_start_sample = t->current_vs_start_sample;

    t->parity ^= 1u;
    ++t->field_id;
    t->line_id = 0u;
    t->hsyncs_since_field_start = 0u;

    if (!first_field && !t->v_locked) {
        t->v_locked = true;
        ++t->lock_acquisitions;
    }
    ++t->vertical_intervals;

    event->valid = true;
    event->field_start = true;
    event->field_id = t->field_id;
    event->parity = t->parity;
    event->line_id = 0u;
    *emitted = true;
}

static void note_grid_phase(c5vrx_video_timing_state_t *t, uint64_t start)
{
    if (!t->last_hsync_sample) {
        t->vs_phase_class = -1;
        return;
    }
    const uint32_t line = t->line_period_samples;
    const uint32_t residual = (uint32_t)((start - t->last_hsync_sample) % line);
    t->vs_phase_class =
        (residual > line / 4u && residual < 3u * line / 4u) ? 1 : 0;
}

static void enter_vertical_interval(c5vrx_video_timing_state_t *t,
                                    uint64_t start)
{
    t->vs_phase = VS_PHASE_PRE_EQUALIZING;
    t->pre_equalizers = 1u;
    t->serrations = 0u;
    t->post_equalizers = 0u;
    t->in_vertical_interval = true;
    t->current_vs_start_sample = start;
    note_grid_phase(t, start);
}

static void abort_vertical_interval(c5vrx_video_timing_state_t *t)
{
    ++t->false_vertical_candidates;
    t->vs_phase = VS_PHASE_ACTIVE;
    t->in_vertical_interval = false;
}

static void handle_pulse(c5vrx_video_timing_state_t *t,
                         c5vrx_composite_polarity_t origin,
                         c5vrx_polarity_engine_t *engine,
                         uint64_t start,
                         uint32_t width,
                         c5vrx_video_timing_event_t *event,
                         bool *emitted)
{
    if (t->polarity != C5VRX_COMPOSITE_POLARITY_UNKNOWN &&
        origin != t->polarity) {
        /* Once polarity is locked, opposite-polarity candidates are noise:
         * active-video/color edges must never relocate sync (PR #9). */
        ++t->opposite_polarity_candidates_rejected;
        return;
    }

    if (width_is_broad(t, width)) {
        switch (t->vs_phase) {
            case VS_PHASE_ACTIVE:
                enter_vertical_interval(t, start);
                t->pre_equalizers = 0u; /* Degraded entry: equalizers missed. */
                t->vs_phase = VS_PHASE_SERRATED;
                t->serrations = 1u;
                event->valid = true;
                event->vertical_interval = true;
                *emitted = true;
                break;
            case VS_PHASE_PRE_EQUALIZING:
                if (t->pre_equalizers < MAX_PRE_EQ_PULSES) {
                    t->vs_phase = VS_PHASE_SERRATED;
                    t->serrations = 1u;
                } else {
                    abort_vertical_interval(t);
                }
                break;
            case VS_PHASE_SERRATED:
                if (++t->serrations > MAX_SERRATIONS) {
                    abort_vertical_interval(t);
                }
                break;
            case VS_PHASE_POST_EQUALIZING:
                abort_vertical_interval(t);
                break;
        }
        return;
    }

    if (width_is_equalizing(t, width)) {
        switch (t->vs_phase) {
            case VS_PHASE_ACTIVE:
                if (t->h_locked) enter_vertical_interval(t, start);
                break;
            case VS_PHASE_PRE_EQUALIZING:
                if (++t->pre_equalizers > MAX_PRE_EQ_PULSES) {
                    abort_vertical_interval(t);
                }
                break;
            case VS_PHASE_SERRATED:
                t->vs_phase = VS_PHASE_POST_EQUALIZING;
                t->post_equalizers = 1u;
                break;
            case VS_PHASE_POST_EQUALIZING:
                if (++t->post_equalizers > MAX_POST_EQ_PULSES) {
                    abort_vertical_interval(t);
                }
                break;
        }
        return;
    }

    if (!width_is_hsync(t, width)) {
        ++t->rejected_pulses;
        return;
    }

    /* Normal-width H-sync candidate. */
    bool plausible = false;
    engine_note_interval(engine, t, start, &plausible);
    cast_polarity_vote(t, origin, plausible);
    /* Always advance the sync timestamp: the completing H-sync of a
     * vertical interval returns early below, and a stale timestamp would
     * fire the next-sample timeout as a phantom lock loss. */
    t->last_hsync_sample = start;

    if (t->vs_phase == VS_PHASE_POST_EQUALIZING) {
        if (t->post_equalizers >= MIN_POST_EQ_PULSES) {
            complete_vertical_interval(t, event, emitted);
        } else {
            abort_vertical_interval(t);
        }
        return;
    }
    if (t->vs_phase != VS_PHASE_ACTIVE) {
        /* Stray normal-width pulse inside the vertical interval. One is
         * serration jitter; a full-line-spaced one means the interval
         * structure is already over (missed serrations/post-equalizers)
         * and the field must be abandoned instead of wedging the state
         * machine in SERRATED forever. */
        const uint64_t since_previous = start - engine->last_hsync_start;
        ++t->stray_hsync_in_vertical_interval;
        if (since_previous >
            (uint64_t)t->line_period_samples * 9u / 10u) {
            abort_vertical_interval(t);
            /* Fall through: re-process this pulse as an active sync. */
        } else {
            return;
        }
    }

    if (!t->h_locked && engine->interval_score >= H_LOCK_SCORE &&
        (t->polarity == C5VRX_COMPOSITE_POLARITY_UNKNOWN ||
         t->polarity == origin)) {
        t->h_locked = true;
        ++t->lock_acquisitions;
    }
    if (t->h_locked) {
        ++t->hsyncs_since_field_start;
        ++t->line_id;
        t->last_hsync_sample = start;
        ++t->horizontal_events;
        event->valid = true;
        event->horizontal = true;
        event->line_id = t->line_id;
        event->line_period_samples = t->line_period_samples;
        *emitted = true;
    }
}

bool c5vrx_video_timing_consume(c5vrx_video_timing_state_t *t,
                                uint8_t sample,
                                c5vrx_video_timing_event_t *event)
{
    if (!t || !event) return false;
    memset(event, 0, sizeof(*event));
    sample &= 0x3fu;
    envelope_update(t, sample);

    bool emitted = false;
    bool lock_lost = false;
    const uint64_t index = t->samples_seen;

    if (t->h_locked && t->last_hsync_sample && !t->in_vertical_interval &&
        index - t->last_hsync_sample >
            (uint64_t)t->line_period_samples * HSYNC_TIMEOUT_NUM /
                HSYNC_TIMEOUT_DEN) {
        lose_locks(t, &lock_lost);
    }

    const uint8_t low = threshold_low(t);
    const uint8_t high = threshold_high(t);

    /* Negative-polarity engine: sync dips below the low threshold. */
    if (!t->negative_engine.in_pulse) {
        if (sample <= low) {
            t->negative_engine.in_pulse = true;
            t->negative_engine.pulse_start = index;
            t->negative_engine.pulse_length = 1u;
            t->negative_engine.quiet_run = 0u;
        }
    } else {
        ++t->negative_engine.pulse_length;
        if (sample > (uint8_t)(low + 2u)) {
            if (++t->negative_engine.quiet_run >= PULSE_END_GUARD_SAMPLES) {
                const uint32_t width =
                    t->negative_engine.pulse_length - t->negative_engine.quiet_run;
                t->negative_engine.in_pulse = false;
                handle_pulse(t, C5VRX_COMPOSITE_POLARITY_NEGATIVE,
                             &t->negative_engine, t->negative_engine.pulse_start,
                             width, event, &emitted);
            }
        } else {
            t->negative_engine.quiet_run = 0u;
        }
    }

    /* Positive-polarity engine: inverted path raises above the ceiling.
     * Bright picture content can poke above this threshold, but only
     * interval-consistent H-width pulses ever earn votes or lock. */
    if (!t->positive_engine.in_pulse) {
        if (sample >= high) {
            t->positive_engine.in_pulse = true;
            t->positive_engine.pulse_start = index;
            t->positive_engine.pulse_length = 1u;
            t->positive_engine.quiet_run = 0u;
        }
    } else {
        ++t->positive_engine.pulse_length;
        if (sample < (uint8_t)(high - 2u)) {
            if (++t->positive_engine.quiet_run >= PULSE_END_GUARD_SAMPLES) {
                const uint32_t width =
                    t->positive_engine.pulse_length - t->positive_engine.quiet_run;
                t->positive_engine.in_pulse = false;
                handle_pulse(t, C5VRX_COMPOSITE_POLARITY_POSITIVE,
                             &t->positive_engine, t->positive_engine.pulse_start,
                             width, event, &emitted);
            }
        } else {
            t->positive_engine.quiet_run = 0u;
        }
    }

    if (lock_lost) {
        event->valid = true;
        event->lock_lost = true;
        emitted = true;
    }

    event->sample_index = index;
    event->sample_phase = t->last_hsync_sample ?
        (uint32_t)(index - t->last_hsync_sample) : 0u;
    ++t->samples_seen;
    return emitted;
}

void c5vrx_video_timing_init(c5vrx_video_timing_state_t *state,
                             const c5vrx_video_timing_config_t *config)
{
    if (!state) return;
    memset(state, 0, sizeof(*state));
    state->cfg.minimum_line_samples =
        config && config->minimum_line_samples ?
            config->minimum_line_samples : DEFAULT_MIN_LINE;
    state->cfg.maximum_line_samples =
        config && config->maximum_line_samples ?
            config->maximum_line_samples : DEFAULT_MAX_LINE;
    state->cfg.standard_lock_frames =
        config ? config->standard_lock_frames : 0u;
    state->cfg.polarity_lock_votes =
        config ? config->polarity_lock_votes : 0u;
    state->sync_floor = 8u;
    state->signal_peak = 24u;
    state->line_period_samples = 1280u;
    state->vs_phase_class = -1;
    state->last_vs_phase_class = -1;
}

uint32_t c5vrx_video_timing_invalidate(c5vrx_video_timing_state_t *state)
{
    if (!state) return 0u;
    ++state->stream_epoch;
    state->field_id = 0u;
    state->parity = 0u;
    state->line_id = 0u;
    state->sample_phase = 0u;
    state->standard = C5VRX_VIDEO_STANDARD_UNKNOWN;
    state->standard_candidate = C5VRX_VIDEO_STANDARD_UNKNOWN;
    state->standard_agreeing_frames = 0u;
    state->polarity = C5VRX_COMPOSITE_POLARITY_UNKNOWN;
    state->h_locked = false;
    state->v_locked = false;
    state->vs_phase = VS_PHASE_ACTIVE;
    state->in_vertical_interval = false;
    state->pre_equalizers = 0u;
    state->serrations = 0u;
    state->post_equalizers = 0u;
    state->hsyncs_since_field_start = 0u;
    state->current_vs_start_sample = 0u;
    state->last_vs_start_sample = 0u;
    state->last_field_duration_samples = 0u;
    state->vs_phase_class = -1;
    state->last_vs_phase_class = -1;
    engine_reset(&state->negative_engine);
    engine_reset(&state->positive_engine);
    state->last_hsync_sample = 0u;
    return state->stream_epoch;
}
