/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_auto_av.h"

#include <inttypes.h>
#include <string.h>

#include "sdkconfig.h"

#if CONFIG_C5VRX_AUTO_A1_AV

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "ulp_lp_core.h"

#include "c5vrx_lp_av.h"
#include "c5vrx_adc_dump.h"
#include "c5vrx_cvbs_out.h"
#include "c5vrx_rf_dump_producer.h"

#define LP_COMMAND_NONE       0u
#define LP_COMMAND_BOUNDED    1u
#define LP_COMMAND_CONTINUOUS 2u

#define LP_STATE_READY       1u
#define LP_STATE_ARMING      2u
#define LP_STATE_RUNNING     3u
#define LP_STATE_DONE        4u
#define LP_STATE_NO_ACTIVITY 5u
#define LP_STATE_REARM_ERROR 6u

#define CALIBRATION_MS      20u
#define CALIBRATION_WINDOWS 3u
#define LEAD_WORDS          8192u
#define MIN_OUTPUT_HZ       4000000u
#define MAX_OUTPUT_HZ       20000000u
#define FALLBACK_RETRY_MS   350u

static const char *TAG = "c5vrx_auto_av";
static portMUX_TYPE s_status_mux = portMUX_INITIALIZER_UNLOCKED;
static c5vrx_auto_av_status_t s_status;
static bool s_started;
static int64_t s_direct_since_us;

extern const uint8_t c5vrx_lp_av_bin_start[]
    asm("_binary_c5vrx_lp_av_bin_start");
extern const uint8_t c5vrx_lp_av_bin_end[]
    asm("_binary_c5vrx_lp_av_bin_end");

static void set_status(c5vrx_auto_av_state_t state, bool rf_activity,
                       uint32_t source_rate_hz, uint32_t output_rate_hz)
{
    portENTER_CRITICAL(&s_status_mux);
    if (s_status.state != state) {
        s_status.state_transitions++;
        s_direct_since_us = state == C5VRX_AUTO_AV_DIRECT_A1 ?
            esp_timer_get_time() : 0;
    }
    s_status.state = state;
    s_status.rf_activity = rf_activity;
    s_status.source_rate_hz = source_rate_hz;
    s_status.output_rate_hz = output_rate_hz;
    s_status.bursts_completed = ulp_c5vrx_bursts_completed;
    s_status.rearms_succeeded = ulp_c5vrx_rearms_succeeded;
    s_status.rearm_failures = ulp_c5vrx_rearm_failures;
    s_status.gap_cycles_total = ulp_c5vrx_gap_cycles_total;
    s_status.gap_cycles_max = ulp_c5vrx_gap_cycles_max;
    s_status.last_gap_cycles = ulp_c5vrx_last_gap_cycles;
    s_status.lp_state = ulp_c5vrx_state;
    s_status.writer_pointer = ulp_c5vrx_last_pointer;
    s_status.lead_acquired = ulp_c5vrx_lead_acquired;
    s_status.block_period_last = ulp_c5vrx_block_period_last;
    s_status.block_period_min = ulp_c5vrx_block_period_min;
    s_status.block_period_max = ulp_c5vrx_block_period_max;
    s_status.phase_error_cycles = ulp_c5vrx_phase_error_cycles;
    s_status.phase_window_blocks = ulp_c5vrx_phase_window_blocks;
    const int64_t expected = (int64_t)ulp_c5vrx_expected_block_cycles *
        ulp_c5vrx_phase_window_blocks;
    s_status.estimated_drift_ppm = expected ?
        (int32_t)((int64_t)ulp_c5vrx_phase_error_cycles * 1000000LL /
                  expected) : 0;
    s_status.continuous_source_rate_hz = ulp_c5vrx_block_period_last ?
        (uint32_t)(((uint64_t)16384u * 48000000u +
                    ulp_c5vrx_block_period_last / 2u) /
                   ulp_c5vrx_block_period_last) : 0u;
    s_status.continuity_uptime_ms = s_direct_since_us ?
        (uint64_t)(esp_timer_get_time() - s_direct_since_us) / 1000u : 0u;
    portEXIT_CRITICAL(&s_status_mux);
}

bool c5vrx_auto_av_owns_rf(void)
{
    return s_started;
}

void c5vrx_auto_av_get_status(c5vrx_auto_av_status_t *status)
{
    if (!status) return;
    portENTER_CRITICAL(&s_status_mux);
    *status = s_status;
    portEXIT_CRITICAL(&s_status_mux);
}

const char *c5vrx_auto_av_state_name(c5vrx_auto_av_state_t state)
{
    switch (state) {
        case C5VRX_AUTO_AV_OFF: return "OFF";
        case C5VRX_AUTO_AV_PAL_FALLBACK: return "PAL_FALLBACK";
        case C5VRX_AUTO_AV_SCANNING_A1: return "SCANNING_A1";
        case C5VRX_AUTO_AV_DIRECT_A1: return "DIRECT_A1";
        case C5VRX_AUTO_AV_FAULT: return "FAULT";
        default: return "UNKNOWN";
    }
}

static uint32_t median3(const uint32_t values[3])
{
    uint32_t a = values[0], b = values[1], c = values[2];
    if (a > b) { const uint32_t t = a; a = b; b = t; }
    if (b > c) { const uint32_t t = b; b = c; c = t; }
    if (a > b) { const uint32_t t = a; a = b; b = t; }
    return b;
}

static bool wait_lp_finished(uint32_t previous_runs, uint32_t timeout_ms)
{
    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(timeout_ms);
    while (xTaskGetTickCount() < deadline) {
        const uint32_t state = ulp_c5vrx_state;
        if (ulp_c5vrx_runs != previous_runs &&
            (state == LP_STATE_DONE || state == LP_STATE_NO_ACTIVITY ||
             state == LP_STATE_REARM_ERROR)) return true;
        vTaskDelay(1);
    }
    return false;
}

static bool bounded_window(uint32_t *source_rate_hz)
{
    const uint32_t previous_runs = ulp_c5vrx_runs;
    ulp_c5vrx_duration_us = CALIBRATION_MS * 1000u;
    ulp_c5vrx_lead_words = LEAD_WORDS;
    ulp_c5vrx_enable_parlio = 0u;
    ulp_c5vrx_command = LP_COMMAND_BOUNDED;
    if (!wait_lp_finished(previous_runs, CALIBRATION_MS + 80u)) return false;

    const uint32_t words = ulp_c5vrx_writer_advance;
    const uint32_t rate = words * (1000u / CALIBRATION_MS);
    const uint32_t output_rate = (rate + 2u) / 4u;
    const bool ok = ulp_c5vrx_state == LP_STATE_DONE &&
        ulp_c5vrx_lead_acquired != 0u &&
        ulp_c5vrx_bursts_completed >= 8u &&
        ulp_c5vrx_rearms_succeeded >= 7u &&
        ulp_c5vrx_rearm_failures == 0u &&
        output_rate >= MIN_OUTPUT_HZ && output_rate <= MAX_OUTPUT_HZ;
    if (source_rate_hz) *source_rate_hz = rate;
    return ok;
}

static void restore_fallback(bool direct_prepared)
{
    /* LP has disabled both engines and restored HP-SRAM ownership first. */
    const esp_err_t stop_err = c5vrx_rf_dump_stop();
    const esp_err_t av_err = direct_prepared ?
        c5vrx_cvbs_direct_rf_finish() : ESP_OK;
    ESP_LOGI(TAG,
             "C5VRX_AUTO_AV_FALLBACK channel=A1 stop=%d av=%d restore=%u canaries=%u",
             (int)stop_err, (int)av_err,
             c5vrx_rf_dump_last_restore_ok() ? 1u : 0u,
             c5vrx_rf_dump_last_canaries_ok() ? 1u : 0u);
    set_status(C5VRX_AUTO_AV_PAL_FALLBACK, false, 0u, 0u);
}

static void auto_av_task(void *arg)
{
    (void)arg;
    set_status(C5VRX_AUTO_AV_PAL_FALLBACK, false, 0u, 0u);
    ESP_LOGI(TAG,
             "C5VRX_AUTO_AV_READY mode=FIXED_A1 mhz=5865 usb_required=0 buttons_required=0 fallback=PAL");

    for (;;) {
        set_status(C5VRX_AUTO_AV_SCANNING_A1, false, 0u, 0u);
        esp_err_t err = c5vrx_rf_dump_configure(
            C5VRX_ADC_DUMP_MAX_SAMPLES, C5VRX_RF_DUMP_MODE_ORDINARY_RX);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "A1 scan configure failed: %s", esp_err_to_name(err));
            set_status(C5VRX_AUTO_AV_FAULT, false, 0u, 0u);
            vTaskDelay(pdMS_TO_TICKS(FALLBACK_RETRY_MS));
            continue;
        }

        uint32_t rates[CALIBRATION_WINDOWS] = {0};
        bool detected = true;
        for (unsigned i = 0; i < CALIBRATION_WINDOWS; ++i) {
            if (!bounded_window(&rates[i])) {
                detected = false;
                break;
            }
        }
        if (!detected) {
            restore_fallback(false);
            vTaskDelay(pdMS_TO_TICKS(FALLBACK_RETRY_MS));
            continue;
        }

        const uint32_t source_rate_hz = median3(rates);
        const uint32_t output_rate_hz = (source_rate_hz + 2u) / 4u;
        err = c5vrx_cvbs_direct_rf_prepare(output_rate_hz);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "A1 direct AV prepare failed: %s", esp_err_to_name(err));
            restore_fallback(false);
            vTaskDelay(pdMS_TO_TICKS(FALLBACK_RETRY_MS));
            continue;
        }

        ulp_c5vrx_duration_us = 0u;
        ulp_c5vrx_lead_words = LEAD_WORDS;
        ulp_c5vrx_enable_parlio = 1u;
        /* C5 PARLIO has an integer PLL divider. Mirror the driver's nearest
         * divider and monitor the resulting producer/consumer phase drift.
         * The LP clock is the precise 48-MHz XTAL for this calculation. */
        uint32_t divider = (240000000u + output_rate_hz / 2u) /
            output_rate_hz;
        if (divider == 0u) divider = 1u;
        const uint32_t actual_output_rate_hz = 240000000u / divider;
        ulp_c5vrx_expected_block_cycles = (uint32_t)(
            ((uint64_t)4096u * 48000000u + actual_output_rate_hz / 2u) /
            actual_output_rate_hz);
        ulp_c5vrx_command = LP_COMMAND_CONTINUOUS;
        const TickType_t arm_deadline =
            xTaskGetTickCount() + pdMS_TO_TICKS(100u);
        while (ulp_c5vrx_state != LP_STATE_RUNNING &&
               ulp_c5vrx_state != LP_STATE_NO_ACTIVITY &&
               ulp_c5vrx_state != LP_STATE_REARM_ERROR &&
               xTaskGetTickCount() < arm_deadline) {
            vTaskDelay(1);
        }

        if (ulp_c5vrx_state != LP_STATE_RUNNING) {
            restore_fallback(true);
            vTaskDelay(pdMS_TO_TICKS(FALLBACK_RETRY_MS));
            continue;
        }

        set_status(C5VRX_AUTO_AV_DIRECT_A1, true,
                   source_rate_hz, actual_output_rate_hz);
        ESP_LOGI(TAG,
                 "C5VRX_AUTO_AV_DIRECT state=ON channel=A1 mhz=5865 source_rate_hz=%" PRIu32 " output_rate_hz=%" PRIu32 " owner=LP_CORE duration=UNBOUNDED",
                 source_rate_hz, actual_output_rate_hz);

        while (ulp_c5vrx_state == LP_STATE_RUNNING ||
               ulp_c5vrx_state == LP_STATE_ARMING) {
            set_status(C5VRX_AUTO_AV_DIRECT_A1, true,
                       source_rate_hz, actual_output_rate_hz);
            vTaskDelay(pdMS_TO_TICKS(100));
        }

        ESP_LOGW(TAG,
                 "C5VRX_AUTO_AV_DIRECT state=LOST lp_state=%" PRIu32 " bursts=%" PRIu32 " rearms=%" PRIu32 " failures=%" PRIu32,
                 ulp_c5vrx_state, ulp_c5vrx_bursts_completed,
                 ulp_c5vrx_rearms_succeeded, ulp_c5vrx_rearm_failures);
        restore_fallback(true);
        vTaskDelay(pdMS_TO_TICKS(FALLBACK_RETRY_MS));
    }
}

esp_err_t c5vrx_auto_av_start(void)
{
    if (s_started) return ESP_ERR_INVALID_STATE;
    if (!c5vrx_rf_dump_memory_reserved()) return ESP_ERR_NOT_SUPPORTED;

    esp_err_t err = ulp_lp_core_load_binary(
        c5vrx_lp_av_bin_start,
        (size_t)(c5vrx_lp_av_bin_end - c5vrx_lp_av_bin_start));
    if (err != ESP_OK) return err;
    const ulp_lp_core_cfg_t cfg = {
        .wakeup_source = ULP_LP_CORE_WAKEUP_SOURCE_HP_CPU,
    };
    err = ulp_lp_core_run((ulp_lp_core_cfg_t *)&cfg);
    if (err != ESP_OK) return err;

    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(100u);
    while (ulp_c5vrx_state != LP_STATE_READY &&
           xTaskGetTickCount() < deadline) {
        vTaskDelay(1);
    }
    if (ulp_c5vrx_state != LP_STATE_READY) return ESP_ERR_TIMEOUT;

    if (xTaskCreate(auto_av_task, "c5vrx_auto_a1", 4096, NULL, 19, NULL) !=
        pdPASS) return ESP_ERR_NO_MEM;
    s_started = true;
    return ESP_OK;
}

#else

esp_err_t c5vrx_auto_av_start(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

void c5vrx_auto_av_get_status(c5vrx_auto_av_status_t *status)
{
    if (status) memset(status, 0, sizeof(*status));
}

const char *c5vrx_auto_av_state_name(c5vrx_auto_av_state_t state)
{
    (void)state;
    return "OFF";
}

bool c5vrx_auto_av_owns_rf(void)
{
    return false;
}

#endif
