/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_auto_av.h"

#include <inttypes.h>
#include <string.h>

#include "sdkconfig.h"

#if CONFIG_C5VRX_AUTO_A1_AV

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "esp_bit_defs.h"
#include "hal/apm_hal.h"
#include "soc/apm_defs.h"
#include "ulp_lp_core.h"

#include "c5vrx_lp_av.h"
#include "c5vrx_adc_dump.h"
#include "c5vrx_cvbs_out.h"
#include "c5vrx_rf_dump_producer.h"
#include "c5vrx_regdma_iq_probe.h"

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
/* Physical Test6 showed that VTX-off windows fail the per-window activity
 * contract while valid VTX-on windows are intentionally burst-gated and can
 * differ by far more than 5%. Three consecutive bounded windows remain the
 * discriminator; their spread is telemetry, not a false-negative gate. */
#define LEAD_WORDS          8192u
#define MIN_OUTPUT_HZ       4000000u
#define MAX_OUTPUT_HZ       20000000u
#define FALLBACK_RETRY_MS   350u
#define RF_SCAN_TIMEOUT_US 20000u
#define LP_CLOCK_HZ        48000000u

static const char *TAG = "c5vrx_auto_av";
static portMUX_TYPE s_status_mux = portMUX_INITIALIZER_UNLOCKED;
static portMUX_TYPE s_rf_park_mux = portMUX_INITIALIZER_UNLOCKED;
static c5vrx_auto_av_status_t s_status;
static bool s_started;

void c5vrx_auto_av_restore_hp_boot_access(void)
{
    /* HP-TEE peripheral permissions survive an HP software reset.  A prior
     * autonomous run may therefore leave GDMA read-only for TEE mode while
     * the next boot is already constructing the PAL fallback.  Reassert the
     * HP core's boot identity and GDMA R/W permission before any PARLIO driver
     * allocation.  The LP core uses REE0 below, so this does not broaden its
     * later read-only GDMA permission. */
    apm_hal_set_master_sec_mode(BIT(APM_MASTER_HPCORE), APM_SEC_MODE_TEE);
    apm_hal_tee_set_peri_access(APM_TEE_CTRL_HP,
                                BIT64(APM_TEE_HP_PERIPH_GDMA),
                                APM_SEC_MODE_TEE,
                                APM_PERM_R | APM_PERM_W);
}
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
    s_status.consumer_pointer = ulp_c5vrx_consumer_pointer;
    s_status.consumer_lead_words = ulp_c5vrx_consumer_lead_words;
    s_status.consumer_lead_min_words = ulp_c5vrx_consumer_lead_min_words;
    s_status.consumer_lead_max_words = ulp_c5vrx_consumer_lead_max_words;
    s_status.consumer_observations = ulp_c5vrx_consumer_observations;
    s_status.consumer_pointer_changes = ulp_c5vrx_consumer_pointer_changes;
    s_status.consumer_wraps = ulp_c5vrx_consumer_wraps;
    s_status.consumer_descriptor_errors =
        ulp_c5vrx_consumer_descriptor_errors;
    s_status.block_period_last = ulp_c5vrx_block_period_last;
    s_status.block_period_min = ulp_c5vrx_block_period_min;
    s_status.block_period_max = ulp_c5vrx_block_period_max;
    s_status.phase_error_cycles = ulp_c5vrx_phase_error_cycles;
    s_status.phase_window_blocks = ulp_c5vrx_phase_window_blocks;
    s_status.lp_fault_cause = ulp_c5vrx_fault_cause;
    s_status.lp_fault_address = ulp_c5vrx_fault_address;
    s_status.lp_fault_pc = ulp_c5vrx_fault_pc;
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

static inline uint32_t IRAM_ATTR hp_cycle_count(void)
{
    uint32_t value;
    __asm__ __volatile__("csrr %0, mcycle" : "=r"(value));
    return value;
}

/* HP SRAM is lent to the RF writer while an LP command is active.  No HP
 * interrupt, scheduler or USB path may run inside that interval.  The LP core
 * restores ownership before publishing a terminal state, then HP may safely
 * leave this IRAM-only parked loop. */
static bool IRAM_ATTR run_lp_parked(uint32_t command, uint32_t timeout_us)
{
    const uint32_t previous_runs = ulp_c5vrx_runs;
    const uint32_t started_at = hp_cycle_count();
    const uint32_t timeout_cycles = timeout_us *
        (uint32_t)CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ;
    bool saw_running = false;
    bool finished = false;

    portENTER_CRITICAL(&s_rf_park_mux);
    ulp_c5vrx_command = command;
    for (;;) {
        const uint32_t state = ulp_c5vrx_state;
        if (ulp_c5vrx_runs != previous_runs) {
            if (state == LP_STATE_RUNNING) saw_running = true;
            if (state == LP_STATE_DONE || state == LP_STATE_NO_ACTIVITY ||
                state == LP_STATE_REARM_ERROR) {
                finished = true;
                break;
            }
        }
        if (timeout_cycles != 0u &&
            (uint32_t)(hp_cycle_count() - started_at) >= timeout_cycles) {
            break;
        }
    }
    portEXIT_CRITICAL(&s_rf_park_mux);
    return finished && (command != LP_COMMAND_CONTINUOUS || saw_running);
}

static bool bounded_window(unsigned window, uint32_t *source_rate_hz)
{
    ulp_c5vrx_duration_us = CALIBRATION_MS * 1000u;
    ulp_c5vrx_lead_words = LEAD_WORDS;
    ulp_c5vrx_enable_parlio = 0u;
    const bool hardware_rearm = c5vrx_regdma_iq_probe_requested() &&
        c5vrx_regdma_iq_probe_arm() == ESP_OK;
    ulp_c5vrx_hardware_rearm = hardware_rearm ? 1u : 0u;
    if (!run_lp_parked(LP_COMMAND_BOUNDED, 0u)) return false;

    const uint32_t words = ulp_c5vrx_writer_advance;
    const uint32_t run_cycles = ulp_c5vrx_run_cycles;
    const uint32_t rate = run_cycles ?
        (uint32_t)(((uint64_t)words * LP_CLOCK_HZ + run_cycles / 2u) /
                   run_cycles) : 0u;
    const uint32_t output_rate = (rate + 2u) / 4u;
    const bool ok = ulp_c5vrx_state == LP_STATE_DONE &&
        ulp_c5vrx_lead_acquired != 0u &&
        ulp_c5vrx_bursts_completed >= 8u &&
        ulp_c5vrx_rearms_succeeded >= 7u &&
        ulp_c5vrx_rearm_failures == 0u &&
        output_rate >= MIN_OUTPUT_HZ && output_rate <= MAX_OUTPUT_HZ;
    if (hardware_rearm) {
        c5vrx_regdma_iq_probe_note_result(ulp_c5vrx_rearms_succeeded,
                                          ulp_c5vrx_rearm_failures);
    }
    ESP_LOGI(TAG,
             "C5VRX_AUTO_AV_CALIBRATION window=%u ok=%u backend=%s words=%" PRIu32 " run_cycles=%" PRIu32 " rate_hz=%" PRIu32 " blocks=%" PRIu32 " rearms=%" PRIu32 " failures=%" PRIu32 " restarts=%" PRIu32 " period_last=%" PRIu32 " period_min=%" PRIu32 " period_max=%" PRIu32,
             window, ok ? 1u : 0u,
             hardware_rearm ? "LP_TRIGGERED_REGDMA" : "LP_AUTOREARM",
             words, run_cycles, rate,
             ulp_c5vrx_bursts_completed, ulp_c5vrx_rearms_succeeded,
             ulp_c5vrx_rearm_failures, ulp_c5vrx_pointer_restarts,
             ulp_c5vrx_block_period_last, ulp_c5vrx_block_period_min,
             ulp_c5vrx_block_period_max);
    if (source_rate_hz) *source_rate_hz = rate;
    return ok;
}

static void restore_fallback(bool direct_prepared)
{
    /* LP disabled the writer and restored SRAM ownership before returning.
     * Keep the configured RF/FE/PBUS session intact across an absent-signal
     * scan.  The public stop path clears PHY PBUS state and made every scan
     * after the first VTX-off result blind until a full Wi-Fi retune. */
    const esp_err_t av_err = direct_prepared ?
        c5vrx_cvbs_direct_rf_finish() : ESP_OK;
    ESP_LOGI(TAG,
             "C5VRX_AUTO_AV_FALLBACK channel=A1 session=HELD av=%d lp_restore=%u canaries=%u",
             (int)av_err,
             (ulp_c5vrx_state == LP_STATE_DONE ||
              ulp_c5vrx_state == LP_STATE_NO_ACTIVITY ||
              ulp_c5vrx_state == LP_STATE_REARM_ERROR) ? 1u : 0u,
             c5vrx_rf_dump_canaries_intact() ? 1u : 0u);
    set_status(C5VRX_AUTO_AV_PAL_FALLBACK, false, 0u, 0u);
}

static void auto_av_task(void *arg)
{
    (void)arg;
    set_status(C5VRX_AUTO_AV_PAL_FALLBACK, false, 0u, 0u);
    ESP_LOGI(TAG,
             "C5VRX_AUTO_AV_READY mode=FIXED_A1 mhz=5865 usb_required=0 buttons_required=0 fallback=PAL scan_timeout_us=%u retry_ms=%u",
             RF_SCAN_TIMEOUT_US, FALLBACK_RETRY_MS);

    /* Configure once and preserve this exact front-end session. Repeated
     * bounded windows only lend/restore SRAM and toggle the dump writer. */
    esp_err_t err = c5vrx_rf_dump_configure(
        C5VRX_ADC_DUMP_MAX_SAMPLES, C5VRX_RF_DUMP_MODE_ORDINARY_RX);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "A1 persistent RF configure failed: %s",
                 esp_err_to_name(err));
        set_status(C5VRX_AUTO_AV_FAULT, false, 0u, 0u);
        vTaskDelete(NULL);
        return;
    }

    for (;;) {
        set_status(C5VRX_AUTO_AV_SCANNING_A1, false, 0u, 0u);
        uint32_t rates[CALIBRATION_WINDOWS] = {0};
        bool detected = true;
        for (unsigned i = 0; i < CALIBRATION_WINDOWS; ++i) {
            if (!bounded_window(i + 1u, &rates[i])) {
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
        uint32_t rate_min_hz = rates[0];
        uint32_t rate_max_hz = rates[0];
        for (unsigned i = 1u; i < CALIBRATION_WINDOWS; ++i) {
            if (rates[i] < rate_min_hz) rate_min_hz = rates[i];
            if (rates[i] > rate_max_hz) rate_max_hz = rates[i];
        }
        const uint32_t rate_spread_ppm = source_rate_hz ?
            (uint32_t)(((uint64_t)(rate_max_hz - rate_min_hz) * 1000000u) /
                       source_rate_hz) : UINT32_MAX;
        ESP_LOGI(TAG,
                 "C5VRX_AUTO_AV_CADENCE accepted=1 samples=%u,%u,%u median=%u minimum=%u spread_ppm=%u policy=THREE_ACTIVE_WINDOWS",
                 (unsigned)rates[0], (unsigned)rates[1],
                 (unsigned)rates[2], (unsigned)source_rate_hz,
                 (unsigned)rate_min_hz, (unsigned)rate_spread_ppm);
        /* Never clock the four-to-one consumer faster than the slowest
         * observed active window. This preserves the half-ring lead instead
         * of selecting a median rate already faster than one producer
         * window. */
        const uint32_t output_rate_hz = rate_min_hz / 4u;
        err = c5vrx_cvbs_direct_rf_prepare(output_rate_hz);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "A1 direct AV prepare failed: %s", esp_err_to_name(err));
            restore_fallback(false);
            vTaskDelay(pdMS_TO_TICKS(FALLBACK_RETRY_MS));
            continue;
        }

        uint32_t gdma_channel = 0u;
        uint32_t descriptor_base = 0u;
        err = c5vrx_cvbs_direct_rf_dma_info(&gdma_channel,
                                             &descriptor_base);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "A1 GDMA lead monitor unavailable: %s",
                     esp_err_to_name(err));
            restore_fallback(true);
            vTaskDelay(pdMS_TO_TICKS(FALLBACK_RETRY_MS));
            continue;
        }

        ulp_c5vrx_duration_us = 0u;
        ulp_c5vrx_lead_words = LEAD_WORDS;
        ulp_c5vrx_enable_parlio = 1u;
        const bool hardware_rearm = c5vrx_regdma_iq_probe_requested() &&
            c5vrx_regdma_iq_probe_arm() == ESP_OK;
        ulp_c5vrx_hardware_rearm = hardware_rearm ? 1u : 0u;
        ulp_c5vrx_gdma_channel = gdma_channel;
        ulp_c5vrx_gdma_descriptor_base = descriptor_base;
        /* C5 PARLIO has an integer PLL divider. Round the divider upward so
         * the realized consumer clock never exceeds the slowest measured
         * producer window. The LP clock is the precise 48-MHz XTAL used by
         * the phase telemetry. */
        uint32_t divider = (240000000u + output_rate_hz - 1u) /
            output_rate_hz;
        if (divider == 0u) divider = 1u;
        const uint32_t actual_output_rate_hz = 240000000u / divider;
        ulp_c5vrx_expected_block_cycles = (uint32_t)(
            ((uint64_t)4096u * 48000000u + actual_output_rate_hz / 2u) /
            actual_output_rate_hz);
        set_status(C5VRX_AUTO_AV_DIRECT_A1, true,
                   source_rate_hz, actual_output_rate_hz);
        ESP_LOGI(TAG,
                 "C5VRX_AUTO_AV_HP_PARK state=ENTER channel=A1 mhz=5865 backend=%s usb=PAUSED_UNTIL_SIGNAL_LOSS owner=LP_CORE duration=UNBOUNDED",
                 hardware_rearm ? "LP_TRIGGERED_REGDMA" : "LP_AUTOREARM");
        /* The scheduler is deliberately parked, so IDLE cannot feed its task
         * watchdog subscription. Remove only that subscription; LP activity
         * timeout remains the RF safety watchdog and restores ownership on
         * signal loss. Re-add IDLE immediately after normal HP operation
         * resumes. */
        TaskHandle_t idle_task = xTaskGetIdleTaskHandleForCore(0);
        const esp_err_t idle_wdt_remove = idle_task ?
            esp_task_wdt_delete(idle_task) : ESP_ERR_NOT_FOUND;
        const bool ran_direct = run_lp_parked(LP_COMMAND_CONTINUOUS, 0u);
        if (hardware_rearm) {
            c5vrx_regdma_iq_probe_note_result(ulp_c5vrx_rearms_succeeded,
                                              ulp_c5vrx_rearm_failures);
        }
        const esp_err_t idle_wdt_restore =
            idle_wdt_remove == ESP_OK ? esp_task_wdt_add(idle_task) :
            idle_wdt_remove;

        if (!ran_direct) {
            restore_fallback(true);
            vTaskDelay(pdMS_TO_TICKS(FALLBACK_RETRY_MS));
            continue;
        }

        ESP_LOGW(TAG,
                 "C5VRX_AUTO_AV_HP_PARK state=EXIT reason=SIGNAL_LOST lp_state=%" PRIu32 " bursts=%" PRIu32 " rearms=%" PRIu32 " failures=%" PRIu32 " idle_wdt_remove=%d idle_wdt_restore=%d",
                 ulp_c5vrx_state, ulp_c5vrx_bursts_completed,
                 ulp_c5vrx_rearms_succeeded, ulp_c5vrx_rearm_failures,
                 (int)idle_wdt_remove, (int)idle_wdt_restore);
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

    /* The C5 LP core is an APM master.  ulp_lp_core_run() resets that master,
     * so permissions must be installed after LP_STATE_READY, matching the
     * ordering in Espressif's LP-CPU-to-HP-peripheral APM test.  Grant only:
     *   MODEM     0x600a9004/08  RF writer control and pointer
     *   REGDMA    0x60093000..24 PAU link-3 start/completion
     *   SYSTEM    0x60095004     HP-SRAM bank ownership
     *   PCR       0x600960b4     PARLIO peripheral clock gate
     *   PARL_IO   0x60015028/34  deferred FIFO-empty IRQ arm/clear
     */
    const uint64_t lp_hp_rw_peripherals =
        BIT64(APM_TEE_HP_PERIPH_MODEM) |
        BIT64(APM_TEE_HP_PERIPH_REGDMA) |
         BIT64(APM_TEE_HP_PERIPH_SYSTEM_REG) |
         BIT64(APM_TEE_HP_PERIPH_PCR_REG) |
         BIT64(APM_TEE_HP_PERIPH_PARL_IO);
    /* Keep HP in TEE (normal ESP-IDF machine-mode execution) and put LP in a
     * distinct security domain.  Peripheral permissions are selected by
     * security mode, not by master ID: assigning LP to TEE and making GDMA
     * read-only also made the HP driver read-only and caused the reproducible
     * store fault at 0x60080400 on the next PAL allocation. */
    apm_hal_set_master_sec_mode(BIT(APM_MASTER_LPCORE), APM_SEC_MODE_REE0);
    apm_hal_tee_set_peri_access(APM_TEE_CTRL_HP, lp_hp_rw_peripherals,
                                APM_SEC_MODE_REE0, APM_PERM_R | APM_PERM_W);
    apm_hal_tee_set_peri_access(APM_TEE_CTRL_HP,
                                BIT64(APM_TEE_HP_PERIPH_GDMA),
                                APM_SEC_MODE_REE0, APM_PERM_R);
    ESP_LOGI(TAG,
             "C5VRX_AUTO_AV_LP_ACCESS ordering=AFTER_LP_RESET mode=REE0 peripherals=MODEM,SYSTEM_REG,PCR,PARL_IO permissions=RW gdma_permission=R hp_mode=TEE hp_gdma_permission=RW sram_handoff=LP_CORE hp_policy=PARKED");

    if (xTaskCreate(auto_av_task, "c5vrx_auto_a1", 4096, NULL, 19, NULL) !=
        pdPASS) return ESP_ERR_NO_MEM;
    s_started = true;
    return ESP_OK;
}

#else

void c5vrx_auto_av_restore_hp_boot_access(void)
{
}

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
