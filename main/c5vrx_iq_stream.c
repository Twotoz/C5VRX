/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_iq_stream.h"

#include <string.h>
#include "c5vrx_auto_av.h"

#define RF_BLOCK_WORDS 16384u

const char *c5vrx_iq_stream_backend_name(c5vrx_iq_backend_t backend)
{
    switch (backend) {
        case C5VRX_IQ_BACKEND_HW_AUTOREARM: return "HW_AUTOREARM";
        case C5VRX_IQ_BACKEND_NATIVE_GAPLESS: return "NATIVE_GAPLESS";
        default: return "LP_AUTOREARM";
    }
}

esp_err_t c5vrx_iq_stream_get_state(c5vrx_iq_stream_state_t *state)
{
    if (!state) return ESP_ERR_INVALID_ARG;
    c5vrx_auto_av_status_t av;
    c5vrx_auto_av_get_status(&av);
    memset(state, 0, sizeof(*state));
    state->backend = C5VRX_IQ_BACKEND_LP_AUTOREARM;
    state->write_sample = (uint64_t)av.bursts_completed * RF_BLOCK_WORDS +
                          av.writer_pointer;
    state->read_sample = (uint64_t)av.consumer_wraps * RF_BLOCK_WORDS +
                         av.consumer_pointer;
    state->physical_write = av.writer_pointer;
    state->physical_read = av.consumer_pointer;
    state->lead_samples = av.consumer_lead_words;
    state->software_rearms = av.rearms_succeeded;
    state->rearm_failures = av.rearm_failures;
    state->boundary_gap_cycles_max = av.gap_cycles_max;
    state->running = av.state == C5VRX_AUTO_AV_DIRECT_A1;
    state->rf_detected = av.rf_activity;
    /* These remain false until boundary phase and hardware ownership are
     * physically proven.  A monotonic software epoch is not a hardware ring. */
    state->hardware_circular = false;
    state->phase_continuous = false;
    return ESP_OK;
}
