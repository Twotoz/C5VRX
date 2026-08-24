/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    C5VRX_AV_HEALTH_STARTING = 0,
    C5VRX_AV_HEALTH_OK,
    C5VRX_AV_HEALTH_WARN,
    C5VRX_AV_HEALTH_FAIL,
} c5vrx_av_health_t;

typedef struct {
    bool running;
    bool task_running;
    uint32_t service_count;
    uint64_t uptime_us;
    uint64_t last_service_age_us;
    uint32_t service_max_us;
    uint32_t deadline_us;
    uint32_t missed_switches;
    uint32_t unexpected_switches;
    uint32_t queue_errors;
} c5vrx_av_health_input_t;

c5vrx_av_health_t c5vrx_av_health_classify(
    const c5vrx_av_health_input_t *input);
const char *c5vrx_av_health_name(c5vrx_av_health_t health);
