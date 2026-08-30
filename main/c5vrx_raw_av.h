/* SPDX-License-Identifier: GPL-3.0-only */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "c5vrx_iq_video.h"

typedef enum {
    C5VRX_RAW_AV_NO_RF = 0,
    C5VRX_RAW_AV_ACQUIRE,
    C5VRX_RAW_AV_LIVE,
    C5VRX_RAW_AV_HOLDOVER,
} c5vrx_raw_av_state_t;

typedef struct {
    c5vrx_raw_av_state_t state;
    c5vrx_iq_video_result_t video;
    uint64_t blocks;
    uint64_t transform_us_total;
    uint32_t transform_us_max;
    uint64_t gap_ns_total;
    uint32_t gap_ns_max;
    uint32_t holdover_blocks;
    bool live_ready;
} c5vrx_raw_av_status_t;

esp_err_t c5vrx_raw_av_analyze(void);
esp_err_t c5vrx_raw_av_start(void);
esp_err_t c5vrx_raw_av_stop(void);
bool c5vrx_raw_av_running(void);
void c5vrx_raw_av_get_status(c5vrx_raw_av_status_t *status);
const char *c5vrx_raw_av_state_name(c5vrx_raw_av_state_t state);
void c5vrx_raw_av_print_analysis(void);
void c5vrx_raw_av_print_status(void);
