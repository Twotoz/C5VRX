/* SPDX-License-Identifier: GPL-3.0-only */

#include <assert.h>
#include <stdio.h>
#include "c5vrx_av_health.h"

int main(void)
{
    c5vrx_av_health_input_t in = {
        .running = true, .task_running = true, .service_count = 10,
        .uptime_us = 100000, .last_service_age_us = 100,
        .service_max_us = 500, .deadline_us = 1280,
    };
    assert(c5vrx_av_health_classify(&in) == C5VRX_AV_HEALTH_OK);
    in.service_max_us = 1281;
    assert(c5vrx_av_health_classify(&in) == C5VRX_AV_HEALTH_WARN);
    in.service_max_us = 500;
    in.missed_switches = 1;
    assert(c5vrx_av_health_classify(&in) == C5VRX_AV_HEALTH_WARN);
    in.last_service_age_us = 5001;
    assert(c5vrx_av_health_classify(&in) == C5VRX_AV_HEALTH_FAIL);
    in.last_service_age_us = 0;
    in.missed_switches = 0;
    in.service_count = 0;
    in.uptime_us = 10000;
    assert(c5vrx_av_health_classify(&in) == C5VRX_AV_HEALTH_STARTING);
    in.uptime_us = 21000;
    assert(c5vrx_av_health_classify(&in) == C5VRX_AV_HEALTH_FAIL);
    puts("C5VRX_AV_HEALTH_TEST result=PASS");
    return 0;
}
