/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

typedef enum {
    C5VRX_IQ_BACKEND_LP_AUTOREARM = 0,
    C5VRX_IQ_BACKEND_HW_AUTOREARM,
    C5VRX_IQ_BACKEND_NATIVE_GAPLESS,
} c5vrx_iq_backend_t;

typedef struct {
    uint64_t write_sample;
    uint64_t read_sample;
    uint64_t hardware_wraps;
    uint32_t physical_write;
    uint32_t physical_read;
    uint32_t lead_samples;
    uint32_t software_rearms;
    uint32_t rearm_failures;
    uint32_t boundary_gap_cycles_max;
    bool running;
    bool hardware_circular;
    bool phase_continuous;
    bool rf_detected;
    c5vrx_iq_backend_t backend;
} c5vrx_iq_stream_state_t;

/* Producer-neutral view of the active IQ stream.  PR21's measured LP rearm
 * path is the only selectable backend until a hardware probe passes. */
esp_err_t c5vrx_iq_stream_get_state(c5vrx_iq_stream_state_t *state);
const char *c5vrx_iq_stream_backend_name(c5vrx_iq_backend_t backend);
