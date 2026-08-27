/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

typedef enum {
    C5VRX_NATIVE_RING_CONDITION_VTX_OFF = 0,
    C5VRX_NATIVE_RING_CONDITION_VTX_ON,
    C5VRX_NATIVE_RING_CONDITION_COHERENT_TONE,
} c5vrx_native_ring_condition_t;

typedef enum {
    C5VRX_NATIVE_RING_INCONCLUSIVE = 0,
    C5VRX_NATIVE_RING_REJECTED,
    C5VRX_NATIVE_RING_PROVEN,
} c5vrx_native_ring_classification_t;

typedef struct {
    uint32_t duration_ms;
    uint32_t observations;
    uint32_t pointer_changes;
    uint32_t hardware_wrap_count;
    uint32_t minimum_pointer;
    uint32_t maximum_pointer;
    uint32_t physical_writer_pointer;
    uint64_t absolute_writer_samples;
    uint32_t enable_assertions;
    uint32_t enable_low_observations;
    uint32_t mode_low_observations;
    uint32_t terminal_done_observations;
    uint32_t progress_after_done;
    uint32_t software_trigger_pulses;
    uint32_t software_rearms;
    uint32_t trigger_high_observations;
    uint32_t ambiguous_backward_observations;
    uint32_t content_observations;
    uint32_t content_changes;
    uint32_t wrap_content_changes;
    uint32_t fixed_epoch_observations;
    uint32_t fixed_epoch_changes;
    uint32_t fixed_epoch_signature;
    uint32_t iq_power_mean;
    uint32_t content_signature;
    uint32_t phase_boundary_observations;
    uint32_t phase_boundary_residual_abs_mean;
    uint32_t phase_boundary_residual_abs_max;
    uint32_t start_control;
    uint32_t final_control;
    uint32_t fault_reason;
    bool engine_enabled_throughout;
    bool writer_stopped_after_done;
    bool pointer_ring_pass;
    bool memory_ring_pass;
    bool structural_pass;
    bool sequence_valid;
    bool rf_distinguishable;
    bool phase_continuous;
    c5vrx_native_ring_classification_t classification;
} c5vrx_native_ring_stats_t;

esp_err_t c5vrx_native_ring_init(void);
bool c5vrx_native_ring_available(void);
esp_err_t c5vrx_native_ring_av_start(void);
bool c5vrx_native_ring_av_running(void);
esp_err_t c5vrx_native_ring_probe(c5vrx_native_ring_condition_t condition,
                                  uint32_t duration_ms,
                                  c5vrx_native_ring_stats_t *stats);
bool c5vrx_native_ring_get_last(c5vrx_native_ring_condition_t *condition,
                                c5vrx_native_ring_stats_t *stats);
const char *c5vrx_native_ring_condition_name(
    c5vrx_native_ring_condition_t condition);
const char *c5vrx_native_ring_classification_name(
    c5vrx_native_ring_classification_t classification);
