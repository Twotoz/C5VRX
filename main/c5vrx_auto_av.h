/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

typedef enum {
    C5VRX_AUTO_AV_OFF = 0,
    C5VRX_AUTO_AV_PAL_FALLBACK,
    C5VRX_AUTO_AV_SCANNING_A1,
    C5VRX_AUTO_AV_DIRECT_A1,
    C5VRX_AUTO_AV_FAULT,
} c5vrx_auto_av_state_t;

typedef struct {
    c5vrx_auto_av_state_t state;
    bool rf_activity;
    uint32_t source_rate_hz;
    uint32_t continuous_source_rate_hz;
    uint32_t output_rate_hz;
    uint32_t bursts_completed;
    uint32_t rearms_succeeded;
    uint32_t rearm_failures;
    uint32_t gap_cycles_total;
    uint32_t gap_cycles_max;
    uint32_t last_gap_cycles;
    uint32_t lp_state;
    uint32_t writer_pointer;
    uint32_t lead_acquired;
    uint32_t consumer_pointer;
    uint32_t consumer_lead_words;
    uint32_t consumer_lead_min_words;
    uint32_t consumer_lead_max_words;
    uint32_t consumer_observations;
    uint32_t consumer_pointer_changes;
    uint32_t consumer_wraps;
    uint32_t consumer_descriptor_errors;
    uint32_t block_period_last;
    uint32_t block_period_min;
    uint32_t block_period_max;
    int32_t phase_error_cycles;
    uint32_t phase_window_blocks;
    uint32_t lp_fault_cause;
    uint32_t lp_fault_address;
    uint32_t lp_fault_pc;
    int32_t estimated_drift_ppm;
    uint64_t continuity_uptime_ms;
    uint32_t state_transitions;
} c5vrx_auto_av_status_t;

esp_err_t c5vrx_auto_av_start(void);
void c5vrx_auto_av_get_status(c5vrx_auto_av_status_t *status);
const char *c5vrx_auto_av_state_name(c5vrx_auto_av_state_t state);
bool c5vrx_auto_av_owns_rf(void);
