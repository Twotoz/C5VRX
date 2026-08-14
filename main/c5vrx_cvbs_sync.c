/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_cvbs_sync.h"

#include <stddef.h>
#include <string.h>

#define NOMINAL_LINE_SAMPLES 1280u
#define MIN_LINE_SAMPLES 1100u
#define MAX_LINE_SAMPLES 1450u
#define MIN_HSYNC_SAMPLES 60u
#define MAX_HSYNC_SAMPLES 160u
#define MIN_BROAD_SYNC_SAMPLES 300u
#define MAX_BROAD_SYNC_SAMPLES 800u
#define HORIZONTAL_LOCK_EVENTS 3u
#define MAX_FIELD_LINES 340u
#define VERTICAL_CLUSTER_LINES 4u
#define HORIZONTAL_TIMEOUT_LINES 3u

static void lose_horizontal_lock(c5vrx_cvbs_sync_tracker_t *tracker)
{
    if (tracker->horizontal_locked) ++tracker->lock_losses;
    tracker->horizontal_locked = false;
    tracker->horizontal_lock_score = 0u;
}

void c5vrx_cvbs_sync_init(c5vrx_cvbs_sync_tracker_t *tracker)
{
    if (!tracker) return;
    memset(tracker, 0, sizeof(*tracker));
    tracker->line_period_samples = NOMINAL_LINE_SAMPLES;
    tracker->sync_floor = 8u;
    tracker->signal_peak = 24u;
    tracker->field_line = C5VRX_CVBS_SYNC_NO_LINE;
}

uint8_t c5vrx_cvbs_sync_threshold(const c5vrx_cvbs_sync_tracker_t *tracker)
{
    if (!tracker) return 8u;
    const unsigned span = tracker->signal_peak > tracker->sync_floor ?
        (unsigned)tracker->signal_peak - (unsigned)tracker->sync_floor : 8u;
    unsigned threshold = tracker->sync_floor + span / 4u;
    if (threshold < 4u) threshold = 4u;
    if (threshold > 14u) threshold = 14u;
    return (uint8_t)threshold;
}

static void update_envelope(c5vrx_cvbs_sync_tracker_t *tracker, uint8_t sample)
{
    if (sample < tracker->sync_floor) tracker->sync_floor = sample;
    if (sample > tracker->signal_peak) tracker->signal_peak = sample;

    /* Slow release lets the threshold follow level drift without following
     * individual dirty-video samples or lifting into the black pedestal. */
    if ((tracker->samples_seen & 1023u) == 0u) {
        if (tracker->sync_floor < 8u) ++tracker->sync_floor;
        if (tracker->signal_peak > 20u) --tracker->signal_peak;
    }
}

static bool finish_pulse(c5vrx_cvbs_sync_tracker_t *tracker,
                         c5vrx_cvbs_sync_event_t *event)
{
    const uint32_t width = tracker->pulse_samples - tracker->high_run;
    const uint64_t pulse_start = tracker->pulse_start;
    tracker->in_sync = false;
    tracker->pulse_samples = 0;
    tracker->high_run = 0;

    if (width >= MIN_BROAD_SYNC_SAMPLES && width <= MAX_BROAD_SYNC_SAMPLES) {
        /* Equalising/broad-sync sequences contain several pulses per field.
         * Count and emit the cluster once, not once per constituent pulse. */
        const bool same_cluster = tracker->vertical_events != 0u &&
            pulse_start - tracker->last_vsync_start <
                (uint64_t)VERTICAL_CLUSTER_LINES * tracker->line_period_samples;
        if (same_cluster) return false;
        ++tracker->vertical_events;
        tracker->last_vsync_start = pulse_start;
        tracker->last_vsync_width = width;
        tracker->vertical_locked = true;
        tracker->field_line = 0u;
        tracker->last_hsync_start = 0u;
        lose_horizontal_lock(tracker);
        event->vertical = true;
        event->sync_start = pulse_start;
        event->field_line = 0u;
        return true;
    }
    if (width < MIN_HSYNC_SAMPLES || width > MAX_HSYNC_SAMPLES) {
        ++tracker->rejected_pulses;
        return false;
    }
    ++tracker->horizontal_events;
    tracker->last_hsync_width = width;

    if (tracker->last_hsync_start) {
        const uint64_t interval64 = pulse_start - tracker->last_hsync_start;
        if (interval64 >= MIN_LINE_SAMPLES && interval64 <= MAX_LINE_SAMPLES) {
            const uint32_t interval = (uint32_t)interval64;
            tracker->line_period_samples =
                (tracker->line_period_samples * 7u + interval + 4u) / 8u;
            if (tracker->horizontal_lock_score < HORIZONTAL_LOCK_EVENTS)
                ++tracker->horizontal_lock_score;
        } else if (tracker->horizontal_lock_score) {
            --tracker->horizontal_lock_score;
        }
    }
    if (!tracker->horizontal_locked &&
        tracker->horizontal_lock_score >= HORIZONTAL_LOCK_EVENTS) {
        tracker->horizontal_locked = true;
        ++tracker->lock_acquisitions;
    } else if (tracker->horizontal_locked &&
               tracker->horizontal_lock_score < HORIZONTAL_LOCK_EVENTS) {
        lose_horizontal_lock(tracker);
    }
    tracker->last_hsync_start = pulse_start;

    event->horizontal = true;
    event->sync_start = pulse_start;
    event->line_period_samples = tracker->line_period_samples;
    event->field_line = tracker->vertical_locked ? tracker->field_line :
        C5VRX_CVBS_SYNC_NO_LINE;
    event->locked = tracker->vertical_locked && tracker->horizontal_locked;

    if (tracker->vertical_locked) {
        ++tracker->field_line;
        if (tracker->field_line > MAX_FIELD_LINES) {
            tracker->vertical_locked = false;
            tracker->field_line = C5VRX_CVBS_SYNC_NO_LINE;
            lose_horizontal_lock(tracker);
            event->locked = false;
        }
    }
    return true;
}

bool c5vrx_cvbs_sync_consume(c5vrx_cvbs_sync_tracker_t *tracker,
                             uint8_t sample,
                             c5vrx_cvbs_sync_event_t *event)
{
    if (!tracker || !event) return false;
    memset(event, 0, sizeof(*event));
    event->field_line = C5VRX_CVBS_SYNC_NO_LINE;
    event->sample_index = tracker->samples_seen;
    sample &= 0x3fu;
    update_envelope(tracker, sample);
    const uint8_t threshold = c5vrx_cvbs_sync_threshold(tracker);

    if (tracker->horizontal_locked && tracker->last_hsync_start &&
        tracker->samples_seen - tracker->last_hsync_start >
            (uint64_t)HORIZONTAL_TIMEOUT_LINES * tracker->line_period_samples) {
        tracker->vertical_locked = false;
        tracker->field_line = C5VRX_CVBS_SYNC_NO_LINE;
        lose_horizontal_lock(tracker);
    }

    bool emitted = false;
    if (!tracker->in_sync) {
        if (sample <= threshold) {
            tracker->in_sync = true;
            tracker->pulse_start = tracker->samples_seen;
            tracker->pulse_samples = 1u;
            tracker->high_run = 0u;
        }
    } else {
        ++tracker->pulse_samples;
        if (sample > (uint8_t)(threshold + 2u)) {
            if (++tracker->high_run >= 3u)
                emitted = finish_pulse(tracker, event);
        } else {
            tracker->high_run = 0u;
        }
    }
    ++tracker->samples_seen;
    return emitted;
}
