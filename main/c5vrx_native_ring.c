/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_native_ring.h"

#include <inttypes.h>
#include <string.h>

#include "sdkconfig.h"

#if CONFIG_C5VRX_EXPERIMENTAL_NATIVE_RING_PROBE

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_attr.h"
#include "esp_bit_defs.h"
#include "esp_log.h"
#include "esp_task_wdt.h"
#include "hal/apm_hal.h"
#include "soc/apm_defs.h"
#include "ulp_lp_core.h"

#include "c5vrx_lp_av.h"
#include "c5vrx_cvbs_out.h"
#include "c5vrx_rf_dump_producer.h"

#define LP_COMMAND_NATIVE_RING 4u
#define LP_STATE_READY         1u
#define LP_STATE_RUNNING       3u
#define LP_STATE_DONE          4u
#define LP_STATE_NO_ACTIVITY   5u
#define LP_STATE_REARM_ERROR   6u
#define NATIVE_MIN_DURATION_MS 10u
#define NATIVE_MAX_DURATION_MS 2000u
#define CTRL_ENABLE_BIT         0x80000000u
#define CTRL_MODE_BIT           0x00020000u
#define NATIVE_POINTER_MODE_MASK 0x00fe0000u
#define NATIVE_POINTER_MODE_EXPECTED 0x00080000u
#define NATIVE_AV_OUTPUT_HZ     20000000u
#define NATIVE_AV_LEAD_WORDS    8192u
#define NATIVE_AV_START_DELAY_MS 3000u

static const char *TAG = "c5vrx_native_ring";
static portMUX_TYPE s_probe_mux = portMUX_INITIALIZER_UNLOCKED;
static bool s_initialized;
static bool s_av_started;
static bool s_av_running;
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

#if CONFIG_C5VRX_NATIVE_A1_AV
static void native_av_task(void *arg)
{
    (void)arg;
    vTaskDelay(pdMS_TO_TICKS(NATIVE_AV_START_DELAY_MS));
    esp_err_t err = c5vrx_rf_dump_configure(
        16384u, C5VRX_RF_DUMP_MODE_NATIVE_RING);
    if (err != ESP_OK) goto fail;
    err = c5vrx_cvbs_direct_rf_prepare(NATIVE_AV_OUTPUT_HZ);
    if (err != ESP_OK) goto stop_rf;

    uint32_t gdma_channel = 0u;
    uint32_t descriptor_base = 0u;
    err = c5vrx_cvbs_direct_rf_dma_info(&gdma_channel, &descriptor_base);
    if (err != ESP_OK) goto finish_av;

    ulp_c5vrx_duration_us = 0u;
    ulp_c5vrx_lead_words = NATIVE_AV_LEAD_WORDS;
    ulp_c5vrx_enable_parlio = 1u;
    ulp_c5vrx_gdma_channel = gdma_channel;
    ulp_c5vrx_gdma_descriptor_base = descriptor_base;
    ulp_c5vrx_expected_block_cycles = 0u;
    ESP_LOGW(TAG,
             "C5VRX_NATIVE_AV state=ENTER channel=A1 mhz=5865 pointer_rate_hz=80000000 output_hz=20000000 ring_words=16384 enable_assertions=1 software_triggers=0 software_rearms=0 iq_freshness=PHYSICAL_AV_PENDING hp=PERMANENTLY_PARKED usb=BOOT_DIAGNOSTICS_ONLY");

    TaskHandle_t idle_task = xTaskGetIdleTaskHandleForCore(0);
    const esp_err_t idle_wdt_remove = idle_task ?
        esp_task_wdt_delete(idle_task) : ESP_ERR_NOT_FOUND;
    const uint32_t previous_runs = ulp_c5vrx_runs;
    portENTER_CRITICAL(&s_probe_mux);
    ulp_c5vrx_command = LP_COMMAND_NATIVE_RING;
    for (;;) {
        const uint32_t state = ulp_c5vrx_state;
        if (ulp_c5vrx_runs != previous_runs && state == LP_STATE_RUNNING)
            s_av_running = true;
        if (ulp_c5vrx_runs != previous_runs &&
            (state == LP_STATE_DONE || state == LP_STATE_REARM_ERROR ||
             state == LP_STATE_NO_ACTIVITY)) break;
    }
    portEXIT_CRITICAL(&s_probe_mux);
    s_av_running = false;
    if (idle_wdt_remove == ESP_OK) (void)esp_task_wdt_add(idle_task);
    ESP_LOGE(TAG,
             "C5VRX_NATIVE_AV state=EXIT lp_state=%" PRIu32
             " pointer=%" PRIu32 " wraps=%" PRIu32
             " fixed_epoch_changes=%" PRIu32
             " terminal_done=%" PRIu32 " final_ctrl=%08" PRIx32
             " final_pointer_mode=%08" PRIx32
             " consumer_changes=%" PRIu32 " consumer_wraps=%" PRIu32
             " consumer_descriptor_errors=%" PRIu32
             " fault_reason=%" PRIu32,
             ulp_c5vrx_state, ulp_c5vrx_native_last_pointer,
             ulp_c5vrx_native_wraps, ulp_c5vrx_native_fixed_epoch_changes,
             ulp_c5vrx_native_done_observations,
             ulp_c5vrx_native_final_control,
             ulp_c5vrx_native_final_pointer_mode,
             ulp_c5vrx_consumer_pointer_changes,
             ulp_c5vrx_consumer_wraps,
             ulp_c5vrx_consumer_descriptor_errors,
             ulp_c5vrx_native_fault_reason);

finish_av:
    (void)c5vrx_cvbs_direct_rf_finish();
stop_rf:
    (void)c5vrx_rf_dump_stop();
fail:
    if (err != ESP_OK)
        ESP_LOGE(TAG, "Native A1 AV startup failed: %s", esp_err_to_name(err));
    s_av_started = false;
    vTaskDelete(NULL);
}
#endif

static bool pointer_ring_pass(const c5vrx_native_ring_stats_t *s)
{
    return s->observations >= 1000u && s->pointer_changes >= 100u &&
        s->hardware_wrap_count >= 4u && s->minimum_pointer <= 1024u &&
        s->maximum_pointer >= 15359u && s->enable_assertions == 1u &&
        s->enable_low_observations == 0u &&
        s->mode_low_observations == 0u &&
        s->software_trigger_pulses == 0u && s->software_rearms == 0u &&
        s->trigger_high_observations == 0u &&
        s->ambiguous_backward_observations == 0u &&
        (s->start_control & (CTRL_ENABLE_BIT | CTRL_MODE_BIT)) ==
            (CTRL_ENABLE_BIT | CTRL_MODE_BIT) &&
        (s->final_control & (CTRL_ENABLE_BIT | CTRL_MODE_BIT)) ==
            (CTRL_ENABLE_BIT | CTRL_MODE_BIT) &&
        (s->start_pointer_mode & NATIVE_POINTER_MODE_MASK) ==
            NATIVE_POINTER_MODE_EXPECTED &&
        (s->final_pointer_mode & NATIVE_POINTER_MODE_MASK) ==
            NATIVE_POINTER_MODE_EXPECTED &&
        !s->writer_stopped_after_done &&
        (s->fault_reason == 0u || s->fault_reason == 10u);
}

static bool memory_ring_pass(const c5vrx_native_ring_stats_t *s)
{
    return s->content_changes > 0u &&
        s->fixed_epoch_observations >= 4u &&
        s->fixed_epoch_changes >= 2u;
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
            BIT64(APM_TEE_HP_PERIPH_SYSTEM_REG) |
            BIT64(APM_TEE_HP_PERIPH_PCR_REG) |
            BIT64(APM_TEE_HP_PERIPH_PARL_IO),
        APM_SEC_MODE_REE0, APM_PERM_R | APM_PERM_W);
    apm_hal_tee_set_peri_access(
        APM_TEE_CTRL_HP, BIT64(APM_TEE_HP_PERIPH_GDMA),
        APM_SEC_MODE_REE0, APM_PERM_R);
    s_initialized = true;
    return ESP_OK;
}

bool c5vrx_native_ring_available(void)
{
    return s_initialized && c5vrx_rf_dump_producer_available();
}

esp_err_t c5vrx_native_ring_av_start(void)
{
#if CONFIG_C5VRX_NATIVE_A1_AV
    if (!c5vrx_native_ring_available()) return ESP_ERR_NOT_SUPPORTED;
    if (s_av_started) return ESP_ERR_INVALID_STATE;
    if (xTaskCreate(native_av_task, "c5vrx_native_av", 4096, NULL, 19,
                    NULL) != pdPASS) return ESP_ERR_NO_MEM;
    s_av_started = true;
    return ESP_OK;
#else
    return ESP_ERR_NOT_SUPPORTED;
#endif
}

bool c5vrx_native_ring_av_running(void)
{
    return s_av_running;
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
    if (s_av_started) return ESP_ERR_INVALID_STATE;

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
    stats->fixed_epoch_observations =
        ulp_c5vrx_native_fixed_epoch_observations;
    stats->fixed_epoch_changes = ulp_c5vrx_native_fixed_epoch_changes;
    stats->fixed_epoch_signature = ulp_c5vrx_native_fixed_epoch_signature;
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
    stats->start_pointer_mode = ulp_c5vrx_native_start_pointer_mode;
    stats->final_pointer_mode = ulp_c5vrx_native_final_pointer_mode;
    stats->fault_reason = ulp_c5vrx_native_fault_reason;
    stats->engine_enabled_throughout =
        stats->enable_assertions == 1u &&
        stats->enable_low_observations == 0u;
    stats->writer_stopped_after_done =
        ulp_c5vrx_native_writer_stopped_after_done != 0u;
    stats->pointer_ring_pass = finished && pointer_ring_pass(stats);
    stats->memory_ring_pass = finished && memory_ring_pass(stats);
    stats->structural_pass = stats->pointer_ring_pass &&
        stats->memory_ring_pass && stats->fault_reason == 0u;

    const esp_err_t stop_err = c5vrx_rf_dump_stop();
    if (!finished) return ESP_ERR_TIMEOUT;
    if (stop_err != ESP_OK) return stop_err;

    if (condition == C5VRX_NATIVE_RING_CONDITION_VTX_OFF) {
        stats->sequence_valid = true;
        s_off_valid = stats->structural_pass;
        s_on_valid = false;
        s_rf_distinguishable = false;
        s_off_power = stats->iq_power_mean;
        s_off_signature = stats->content_signature;
    } else if (condition == C5VRX_NATIVE_RING_CONDITION_VTX_ON) {
        stats->sequence_valid = s_off_valid;
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
        stats->sequence_valid = s_off_valid && s_on_valid;
        stats->rf_distinguishable = s_rf_distinguishable;
    }

    stats->phase_continuous =
        condition == C5VRX_NATIVE_RING_CONDITION_COHERENT_TONE &&
        stats->memory_ring_pass &&
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
esp_err_t c5vrx_native_ring_av_start(void) { return ESP_ERR_NOT_SUPPORTED; }
bool c5vrx_native_ring_av_running(void) { return false; }
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
