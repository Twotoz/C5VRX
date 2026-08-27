/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_native_ring.h"

#include <string.h>

#include "sdkconfig.h"

#if CONFIG_C5VRX_EXPERIMENTAL_NATIVE_RING_PROBE

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_attr.h"
#include "esp_bit_defs.h"
#include "hal/apm_hal.h"
#include "soc/apm_defs.h"
#include "ulp_lp_core.h"

#include "c5vrx_lp_av.h"
#include "c5vrx_rf_dump_producer.h"

#define LP_COMMAND_NATIVE_RING 4u
#define LP_STATE_READY         1u
#define LP_STATE_DONE          4u
#define LP_STATE_REARM_ERROR   6u
#define NATIVE_MIN_DURATION_MS 10u
#define NATIVE_MAX_DURATION_MS 2000u
#define CTRL_ENABLE_BIT         0x80000000u
#define CTRL_MODE_BIT           0x00020000u

static portMUX_TYPE s_probe_mux = portMUX_INITIALIZER_UNLOCKED;
static bool s_initialized;
static bool s_off_valid;
static bool s_on_valid;
static bool s_rf_distinguishable;
static uint32_t s_off_power;
static uint32_t s_off_signature;
static bool s_last_valid;
static c5vrx_native_ring_condition_t s_last_condition;
static c5vrx_native_ring_stats_t s_last_stats;

extern const uint8_t c5vrx_lp_av_bin_start[]
    asm("_binary_c5vrx_lp_av_bin_start");
extern const uint8_t c5vrx_lp_av_bin_end[]
    asm("_binary_c5vrx_lp_av_bin_end");

static inline uint32_t IRAM_ATTR hp_cycle_count(void)
{
    uint32_t value;
    __asm__ __volatile__("csrr %0, mcycle" : "=r"(value));
    return value;
}

static bool IRAM_ATTR execute_lp_probe(uint32_t duration_ms)
{
    const uint32_t prior_runs = ulp_c5vrx_runs;
    const uint32_t started = hp_cycle_count();
    const uint32_t timeout_cycles =
        (duration_ms + 250u) * 1000u *
        (uint32_t)CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ;
    bool finished = false;

    portENTER_CRITICAL(&s_probe_mux);
    ulp_c5vrx_duration_us = duration_ms * 1000u;
    ulp_c5vrx_command = LP_COMMAND_NATIVE_RING;
    for (;;) {
        if (ulp_c5vrx_runs != prior_runs &&
            (ulp_c5vrx_state == LP_STATE_DONE ||
             ulp_c5vrx_state == LP_STATE_REARM_ERROR)) {
            finished = true;
            break;
        }
        if ((uint32_t)(hp_cycle_count() - started) >= timeout_cycles) break;
    }
    portEXIT_CRITICAL(&s_probe_mux);
    return finished;
}

static bool structural_pass(const c5vrx_native_ring_stats_t *s)
{
    return s->observations >= 1000u && s->pointer_changes >= 100u &&
        s->hardware_wrap_count >= 4u && s->minimum_pointer <= 1024u &&
        s->maximum_pointer >= 15359u && s->enable_assertions == 1u &&
        s->enable_low_observations == 0u &&
        s->mode_low_observations == 0u &&
        s->software_trigger_pulses == 0u && s->software_rearms == 0u &&
        s->trigger_high_observations == 0u &&
        s->ambiguous_backward_observations == 0u &&
        s->content_changes > 0u && s->wrap_content_changes >= 2u &&
        (s->start_control & (CTRL_ENABLE_BIT | CTRL_MODE_BIT)) ==
            (CTRL_ENABLE_BIT | CTRL_MODE_BIT) &&
        (s->final_control & (CTRL_ENABLE_BIT | CTRL_MODE_BIT)) ==
            (CTRL_ENABLE_BIT | CTRL_MODE_BIT) &&
        !s->writer_stopped_after_done && s->fault_reason == 0u;
}

esp_err_t c5vrx_native_ring_init(void)
{
    if (s_initialized) return ESP_OK;
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
           xTaskGetTickCount() < deadline) vTaskDelay(1);
    if (ulp_c5vrx_state != LP_STATE_READY) return ESP_ERR_TIMEOUT;

    apm_hal_set_master_sec_mode(BIT(APM_MASTER_LPCORE), APM_SEC_MODE_REE0);
    apm_hal_tee_set_peri_access(
        APM_TEE_CTRL_HP,
        BIT64(APM_TEE_HP_PERIPH_MODEM) |
            BIT64(APM_TEE_HP_PERIPH_SYSTEM_REG),
        APM_SEC_MODE_REE0, APM_PERM_R | APM_PERM_W);
    s_initialized = true;
    return ESP_OK;
}

bool c5vrx_native_ring_available(void)
{
    return s_initialized && c5vrx_rf_dump_producer_available();
}

esp_err_t c5vrx_native_ring_probe(c5vrx_native_ring_condition_t condition,
                                  uint32_t duration_ms,
                                  c5vrx_native_ring_stats_t *stats)
{
    if (!stats || condition > C5VRX_NATIVE_RING_CONDITION_COHERENT_TONE ||
        duration_ms < NATIVE_MIN_DURATION_MS ||
        duration_ms > NATIVE_MAX_DURATION_MS) return ESP_ERR_INVALID_ARG;
    memset(stats, 0, sizeof(*stats));
    stats->duration_ms = duration_ms;
    if (!c5vrx_native_ring_available()) return ESP_ERR_NOT_SUPPORTED;

    esp_err_t err = c5vrx_rf_dump_configure(
        16384u, C5VRX_RF_DUMP_MODE_NATIVE_RING);
    if (err != ESP_OK) return err;
    const bool finished = execute_lp_probe(duration_ms);

    stats->observations = ulp_c5vrx_native_observations;
    stats->pointer_changes = ulp_c5vrx_native_pointer_changes;
    stats->hardware_wrap_count = ulp_c5vrx_native_wraps;
    stats->minimum_pointer = ulp_c5vrx_native_min_pointer;
    stats->maximum_pointer = ulp_c5vrx_native_max_pointer;
    stats->physical_writer_pointer = ulp_c5vrx_native_last_pointer;
    stats->absolute_writer_samples =
        (uint64_t)stats->hardware_wrap_count * 16384ULL +
        stats->physical_writer_pointer;
    stats->enable_assertions = ulp_c5vrx_native_enable_assertions;
    stats->enable_low_observations = ulp_c5vrx_native_enable_low;
    stats->mode_low_observations = ulp_c5vrx_native_mode_low;
    stats->terminal_done_observations = ulp_c5vrx_native_done_observations;
    stats->progress_after_done = ulp_c5vrx_native_progress_after_done;
    stats->software_trigger_pulses = ulp_c5vrx_native_software_triggers;
    stats->software_rearms = ulp_c5vrx_native_software_rearms;
    stats->trigger_high_observations = ulp_c5vrx_native_trigger_high;
    stats->ambiguous_backward_observations =
        ulp_c5vrx_native_ambiguous_backwards;
    stats->content_observations = ulp_c5vrx_native_content_observations;
    stats->content_changes = ulp_c5vrx_native_content_changes;
    stats->wrap_content_changes = ulp_c5vrx_native_wrap_content_changes;
    const uint64_t iq_power_sum =
        ((uint64_t)ulp_c5vrx_native_iq_power_sum_high << 32) |
        ulp_c5vrx_native_iq_power_sum_low;
    stats->iq_power_mean = stats->content_observations ?
        (uint32_t)(iq_power_sum / stats->content_observations) : 0u;
    stats->content_signature = ulp_c5vrx_native_content_signature;
    stats->phase_boundary_observations =
        ulp_c5vrx_native_phase_boundaries;
    stats->phase_boundary_residual_abs_mean =
        stats->phase_boundary_observations ?
            ulp_c5vrx_native_phase_residual_abs_sum /
                stats->phase_boundary_observations : 0u;
    stats->phase_boundary_residual_abs_max =
        ulp_c5vrx_native_phase_residual_abs_max;
    stats->start_control = ulp_c5vrx_native_start_control;
    stats->final_control = ulp_c5vrx_native_final_control;
    stats->fault_reason = ulp_c5vrx_native_fault_reason;
    stats->engine_enabled_throughout =
        stats->enable_assertions == 1u &&
        stats->enable_low_observations == 0u;
    stats->writer_stopped_after_done =
        ulp_c5vrx_native_writer_stopped_after_done != 0u;
    stats->structural_pass = finished && structural_pass(stats);

    const esp_err_t stop_err = c5vrx_rf_dump_stop();
    if (!finished) return ESP_ERR_TIMEOUT;
    if (stop_err != ESP_OK) return stop_err;

    if (condition == C5VRX_NATIVE_RING_CONDITION_VTX_OFF) {
        s_off_valid = stats->structural_pass;
        s_on_valid = false;
        s_rf_distinguishable = false;
        s_off_power = stats->iq_power_mean;
        s_off_signature = stats->content_signature;
    } else if (condition == C5VRX_NATIVE_RING_CONDITION_VTX_ON) {
        const uint32_t delta = stats->iq_power_mean > s_off_power ?
            stats->iq_power_mean - s_off_power :
            s_off_power - stats->iq_power_mean;
        const uint32_t minimum_delta = s_off_power / 20u + 32u;
        stats->rf_distinguishable = s_off_valid &&
            stats->content_signature != s_off_signature &&
            delta >= minimum_delta;
        s_on_valid = stats->structural_pass;
        s_rf_distinguishable = stats->rf_distinguishable;
    } else {
        stats->rf_distinguishable = s_rf_distinguishable;
    }

    stats->phase_continuous =
        condition == C5VRX_NATIVE_RING_CONDITION_COHERENT_TONE &&
        stats->phase_boundary_observations >= 8u &&
        stats->phase_boundary_residual_abs_mean <= 16u &&
        stats->phase_boundary_residual_abs_max <= 64u;
    if (!stats->structural_pass) {
        stats->classification = C5VRX_NATIVE_RING_REJECTED;
    } else if (condition == C5VRX_NATIVE_RING_CONDITION_COHERENT_TONE &&
               s_off_valid && s_on_valid && s_rf_distinguishable &&
               stats->phase_continuous) {
        stats->classification = C5VRX_NATIVE_RING_PROVEN;
    } else {
        stats->classification = C5VRX_NATIVE_RING_INCONCLUSIVE;
    }
    s_last_condition = condition;
    s_last_stats = *stats;
    s_last_valid = true;
    return ESP_OK;
}

bool c5vrx_native_ring_get_last(c5vrx_native_ring_condition_t *condition,
                                c5vrx_native_ring_stats_t *stats)
{
    if (!condition || !stats || !s_last_valid) return false;
    *condition = s_last_condition;
    *stats = s_last_stats;
    return true;
}

#else

esp_err_t c5vrx_native_ring_init(void) { return ESP_ERR_NOT_SUPPORTED; }
bool c5vrx_native_ring_available(void) { return false; }
esp_err_t c5vrx_native_ring_probe(c5vrx_native_ring_condition_t condition,
                                  uint32_t duration_ms,
                                  c5vrx_native_ring_stats_t *stats)
{
    (void)condition;
    (void)duration_ms;
    if (stats) memset(stats, 0, sizeof(*stats));
    return ESP_ERR_NOT_SUPPORTED;
}
bool c5vrx_native_ring_get_last(c5vrx_native_ring_condition_t *condition,
                                c5vrx_native_ring_stats_t *stats)
{
    (void)condition;
    (void)stats;
    return false;
}

#endif

const char *c5vrx_native_ring_condition_name(
    c5vrx_native_ring_condition_t condition)
{
    switch (condition) {
        case C5VRX_NATIVE_RING_CONDITION_VTX_OFF: return "VTX_OFF";
        case C5VRX_NATIVE_RING_CONDITION_VTX_ON: return "VTX_ON";
        case C5VRX_NATIVE_RING_CONDITION_COHERENT_TONE: return "TONE";
        default: return "UNKNOWN";
    }
}

const char *c5vrx_native_ring_classification_name(
    c5vrx_native_ring_classification_t classification)
{
    switch (classification) {
        case C5VRX_NATIVE_RING_REJECTED: return "NATIVE_RING_REJECTED";
        case C5VRX_NATIVE_RING_PROVEN: return "NATIVE_RING_PROVEN";
        default: return "INCONCLUSIVE";
    }
}
