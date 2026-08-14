/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define C5VRX_CVBS_SYNC_NO_LINE UINT16_MAX

typedef struct {
    bool horizontal;
    bool vertical;
    bool locked;
    uint16_t field_line;
    uint32_t line_period_samples;
    uint64_t sample_index;
    uint64_t sync_start;
} c5vrx_cvbs_sync_event_t;

typedef struct {
    uint64_t samples_seen;
    uint64_t pulse_start;
    uint64_t last_hsync_start;
    uint64_t last_vsync_start;
    uint32_t line_period_samples;
    uint32_t pulse_samples;
    uint32_t horizontal_events;
    uint32_t vertical_events;
    uint32_t rejected_pulses;
    uint32_t lock_acquisitions;
    uint32_t lock_losses;
    uint32_t last_hsync_width;
    uint32_t last_vsync_width;
    uint16_t field_line;
    uint8_t sync_floor;
    uint8_t signal_peak;
    uint8_t horizontal_lock_score;
    uint8_t high_run;
    bool in_sync;
    bool horizontal_locked;
    bool vertical_locked;
} c5vrx_cvbs_sync_tracker_t;

void c5vrx_cvbs_sync_init(c5vrx_cvbs_sync_tracker_t *tracker);
bool c5vrx_cvbs_sync_consume(c5vrx_cvbs_sync_tracker_t *tracker,
                             uint8_t sample,
                             c5vrx_cvbs_sync_event_t *event);
uint8_t c5vrx_cvbs_sync_threshold(const c5vrx_cvbs_sync_tracker_t *tracker);

#ifdef __cplusplus
}
#endif
