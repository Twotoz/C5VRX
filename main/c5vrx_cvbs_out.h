/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "c5vrx_av_health.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    C5VRX_CVBS_DISPLAY_LOGO = 0,
    C5VRX_CVBS_DISPLAY_SNOW,
    C5VRX_CVBS_DISPLAY_TEST,
} c5vrx_cvbs_display_t;

typedef struct {
    uint16_t hsync_samples;
    uint16_t equalizing_samples;
    uint16_t broad_sync_samples;
    uint8_t pre_equalizing_half_lines;
    uint8_t broad_sync_half_lines;
    uint8_t post_equalizing_half_lines;
} c5vrx_cvbs_timing_t;

typedef struct {
    bool running;
    bool task_running;
    c5vrx_cvbs_display_t display;
    c5vrx_av_health_t health;
    uint32_t retired_buffers;
    uint32_t serviced_buffers;
    uint32_t frame_equivalent;
    uint32_t switch_hz;
    uint32_t expected_switch_hz;
    uint64_t uptime_us;
    uint64_t last_service_age_us;
    uint32_t service_avg_us;
    uint32_t service_max_us;
    uint32_t deadline_us;
    int32_t headroom_us;
    uint32_t missed_switches;
    uint32_t unexpected_switches;
    uint32_t queue_errors;
    uint32_t stack_min_bytes;
} c5vrx_cvbs_output_stats_t;

/** Start the receiver's permanent PAL output in the branded logo state. */
esp_err_t c5vrx_cvbs_output_start(void);

/** Request a tear-free display-state change at the next PAL frame boundary. */
esp_err_t c5vrx_cvbs_output_set_display(c5vrx_cvbs_display_t display);

/** Current on-wire state and its stable protocol/log name. */
c5vrx_cvbs_display_t c5vrx_cvbs_output_display(void);
const char *c5vrx_cvbs_display_name(c5vrx_cvbs_display_t display);

/** Snapshot actual PARLIO buffer retirement and refill health. */
void c5vrx_cvbs_output_get_stats(c5vrx_cvbs_output_stats_t *stats);

/**
 * Queue PAL sync-shape tuning for the next complete frame boundary.
 * Sample widths are at the fixed 20 MS/s output clock. The bounded ranges
 * prevent an experimental GUI setting from removing sync or active video.
 */
esp_err_t c5vrx_cvbs_output_set_timing(const c5vrx_cvbs_timing_t *timing);
void c5vrx_cvbs_output_reset_timing(void);
void c5vrx_cvbs_output_get_timing(c5vrx_cvbs_timing_t *active,
                                  c5vrx_cvbs_timing_t *requested,
                                  bool *pending);

/**
 * Start the analog-output proof generator.
 *
 * The generator streams a PAL 625/50 interlaced monochrome test raster at
 * 20 MS/s through PARLIO. It includes full horizontal/vertical sync structure,
 * branded splash/diagnostics and, when enabled in Kconfig, a PAL-frequency swinging burst
 * used only to stress analog bandwidth/locking.
 *
 * This is intentionally independent from the RF receive path so the
 * C5 -> PARLIO -> passive DAC -> 75-ohm CVBS half can be validated first.
 */
esp_err_t c5vrx_cvbs_test_start(void);

/** Leave diagnostics and return the permanent output to the logo state. */
esp_err_t c5vrx_cvbs_test_stop(void);

/** True while the PAL test stream is active. */
bool c5vrx_cvbs_test_running(void);

/**
 * Replace the rendered PAL source with a paused, looped fixed RF-ring ->
 * BitScrambler -> PARLIO transaction.  A bounded LP-RAM probe starts the
 * PARLIO clock only after the RF writer has acquired a safe lead.
 */
esp_err_t c5vrx_cvbs_direct_rf_prepare(void);
esp_err_t c5vrx_cvbs_direct_rf_finish(void);

#ifdef __cplusplus
}
#endif
