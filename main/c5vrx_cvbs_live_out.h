/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#include "c5vrx_cvbs_sync.h"

typedef struct {
    uint64_t live_blocks;
    uint64_t filler_blocks;
    uint64_t mailbox_drops;
    bool guardian_running;
} c5vrx_cvbs_live_out_stats_t;

esp_err_t c5vrx_cvbs_live_out_start(size_t block_samples);
esp_err_t c5vrx_cvbs_live_out_start_at_rate(size_t block_samples,
                                            uint32_t output_clock_hz);
esp_err_t c5vrx_cvbs_live_out_write(const uint8_t *samples, size_t count,
                                    void *context);
esp_err_t c5vrx_cvbs_live_out_stop(void);
void c5vrx_cvbs_live_out_get_stats(c5vrx_cvbs_live_out_stats_t *stats);
void c5vrx_cvbs_live_out_update_timing(
    const c5vrx_cvbs_sync_tracker_t *timing);
