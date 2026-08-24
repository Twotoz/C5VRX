/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_av_health.h"

c5vrx_av_health_t c5vrx_av_health_classify(
    const c5vrx_av_health_input_t *input)
{
    if (!input || !input->running || !input->task_running) {
        return C5VRX_AV_HEALTH_FAIL;
    }
    if (input->service_count == 0u) {
        return input->uptime_us <= 20000u
            ? C5VRX_AV_HEALTH_STARTING : C5VRX_AV_HEALTH_FAIL;
    }
    if (input->queue_errors || input->unexpected_switches ||
        input->last_service_age_us > 5000u) {
        return C5VRX_AV_HEALTH_FAIL;
    }
    if (input->missed_switches ||
        input->service_max_us > input->deadline_us) {
        return C5VRX_AV_HEALTH_WARN;
    }
    return C5VRX_AV_HEALTH_OK;
}

const char *c5vrx_av_health_name(c5vrx_av_health_t health)
{
    switch (health) {
        case C5VRX_AV_HEALTH_STARTING: return "STARTING";
        case C5VRX_AV_HEALTH_OK: return "OK";
        case C5VRX_AV_HEALTH_WARN: return "WARN";
        case C5VRX_AV_HEALTH_FAIL: return "FAIL";
        default: return "UNKNOWN";
    }
}
