/* SPDX-License-Identifier: GPL-3.0-only */
#include "c5vrx_raw_av.h"

#include <string.h>
#include <stdlib.h>
#include <stddef.h>

#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "c5vrx_adc_dump.h"
#include "c5vrx_cvbs_live_out.h"
#include "c5vrx_cvbs_out.h"
#include "c5vrx_dac.h"
#include "c5vrx_usb_transport.h"
#include "c5vrx_wbfm_hw.h"

#define printf c5vrx_usb_printf
#define ACQUIRE_DC_BLOCKS 3u
#define ACQUIRE_VIDEO_BLOCKS 4u
#define HOLDOVER_LIMIT_BLOCKS 12u
#define RF_BLOCK_TIME_NS 204800u

static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static c5vrx_raw_av_status_t s_status;
static TaskHandle_t s_task;
static volatile bool s_stop;
static c5vrx_wbfm_hw_context_t *s_wbfm;

static void set_state(c5vrx_raw_av_state_t state)
{
    taskENTER_CRITICAL(&s_lock);
    s_status.state = state;
    taskEXIT_CRITICAL(&s_lock);
}

static esp_err_t acquire_analysis(c5vrx_iq_video_result_t *result)
{
    memset(result, 0, sizeof(*result));
    uint32_t *iq = heap_caps_malloc(C5VRX_RF_BLOCK_WORDS * sizeof(uint32_t),
                                    MALLOC_CAP_INTERNAL);
    int16_t *raw = heap_caps_malloc(
        ACQUIRE_VIDEO_BLOCKS * C5VRX_CVBS_BLOCK_SAMPLES * sizeof(int16_t),
        MALLOC_CAP_INTERNAL);
    if (!iq || !raw) {
        free(iq); free(raw);
        return ESP_ERR_NO_MEM;
    }

    result->polarity = 1;
    c5vrx_iq_video_dc_t dc;
    c5vrx_iq_video_dc_init(&dc);
    esp_err_t err = ESP_OK;
    for (unsigned block = 0; block < ACQUIRE_DC_BLOCKS; ++block) {
        err = c5vrx_adc_dump_capture_copy(
            iq, C5VRX_RF_BLOCK_WORDS, NULL);
        if (err != ESP_OK) goto done;
        c5vrx_iq_video_dc_add(&dc, iq, C5VRX_RF_BLOCK_WORDS);
    }
    c5vrx_iq_video_dc_finish(&dc, result);
    for (unsigned block = 0; block < ACQUIRE_VIDEO_BLOCKS; ++block) {
        err = c5vrx_adc_dump_capture_copy(
            iq, C5VRX_RF_BLOCK_WORDS, NULL);
        if (err != ESP_OK) goto done;
        const size_t written = c5vrx_iq_video_demodulate_4to1(
            iq, C5VRX_RF_BLOCK_WORDS, result->i_dc, result->q_dc,
            raw + block * C5VRX_CVBS_BLOCK_SAMPLES,
            C5VRX_CVBS_BLOCK_SAMPLES);
        if (written != C5VRX_CVBS_BLOCK_SAMPLES) {
            err = ESP_FAIL;
            goto done;
        }
    }
    c5vrx_iq_video_analyze_cvbs(
        raw, ACQUIRE_VIDEO_BLOCKS * C5VRX_CVBS_BLOCK_SAMPLES, result);
done:
    free(iq);
    free(raw);
    return err;
}

esp_err_t c5vrx_raw_av_analyze(void)
{
    if (s_task) return ESP_ERR_INVALID_STATE;
    set_state(C5VRX_RAW_AV_ACQUIRE);
    c5vrx_iq_video_result_t result;
    const esp_err_t err = acquire_analysis(&result);
    taskENTER_CRITICAL(&s_lock);
    if (err == ESP_OK) s_status.video = result;
    s_status.live_ready = err == ESP_OK &&
        (result.classification == C5VRX_IQ_VIDEO_PAL_VALID ||
         result.classification == C5VRX_IQ_VIDEO_NTSC_VALID ||
         result.classification == C5VRX_IQ_VIDEO_UNCERTAIN);
    s_status.state = s_status.live_ready ? C5VRX_RAW_AV_HOLDOVER :
                                          C5VRX_RAW_AV_NO_RF;
    taskEXIT_CRITICAL(&s_lock);
    c5vrx_raw_av_print_analysis();
    return err;
}

static void raw_av_task(void *argument)
{
    (void)argument;
    uint32_t *iq = heap_caps_malloc(C5VRX_RF_BLOCK_WORDS * sizeof(uint32_t),
                                    MALLOC_CAP_INTERNAL);
    uint8_t *phase = heap_caps_malloc(C5VRX_CVBS_BLOCK_SAMPLES,
                                      MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    uint8_t *cvbs = heap_caps_malloc(C5VRX_CVBS_BLOCK_SAMPLES,
                                     MALLOC_CAP_INTERNAL);
    uint8_t hold = C5VRX_DAC_BLANK_CODE;
    uint64_t previous_completed_us = 0u;
    if (!iq || !phase || !cvbs) goto finished;

    while (!s_stop) {
        c5vrx_adc_capture_meta_t meta = {0};
        const esp_err_t capture_err = c5vrx_adc_dump_capture_copy(
            iq, C5VRX_RF_BLOCK_WORDS, &meta);
        if (capture_err != ESP_OK) {
            set_state(C5VRX_RAW_AV_HOLDOVER);
            taskENTER_CRITICAL(&s_lock);
            s_status.holdover_blocks++;
            const bool expired = s_status.holdover_blocks >=
                                 HOLDOVER_LIMIT_BLOCKS;
            taskEXIT_CRITICAL(&s_lock);
            if (expired) break;
            continue;
        }
        if (previous_completed_us) {
            const uint64_t interval_ns =
                (meta.completed_us - previous_completed_us) * 1000ull;
            const uint64_t gap_ns = interval_ns > RF_BLOCK_TIME_NS ?
                interval_ns - RF_BLOCK_TIME_NS : 0u;
            taskENTER_CRITICAL(&s_lock);
            s_status.gap_ns_total += gap_ns;
            if (gap_ns > s_status.gap_ns_max)
                s_status.gap_ns_max = gap_ns > UINT32_MAX ? UINT32_MAX :
                                                            (uint32_t)gap_ns;
            taskEXIT_CRITICAL(&s_lock);
        }
        previous_completed_us = meta.completed_us;

        const int64_t transform_begin = esp_timer_get_time();
        size_t written = 0u;
        esp_err_t err = c5vrx_wbfm_hw_transform_context(
            s_wbfm, iq, C5VRX_RF_BLOCK_WORDS, phase,
            C5VRX_CVBS_BLOCK_SAMPLES, &written);
        const uint32_t transform_us = (uint32_t)(esp_timer_get_time() -
                                                 transform_begin);
        if (err != ESP_OK || written != C5VRX_CVBS_BLOCK_SAMPLES) break;
        const int polarity = s_status.video.polarity;
        cvbs[0] = hold; /* no discriminator across a finite RF boundary */
        for (size_t i = 1u; i < written; ++i) {
            int delta = (int)(int8_t)(phase[i] - 128u);
            delta *= polarity;
            cvbs[i] = s_status.video.map[(uint8_t)delta];
        }
        hold = cvbs[written - 1u];
        c5vrx_cvbs_live_out_set_boundary_hold(true, hold);
        err = c5vrx_cvbs_live_out_write_wait(cvbs, written, 10u);
        taskENTER_CRITICAL(&s_lock);
        s_status.transform_us_total += transform_us;
        if (transform_us > s_status.transform_us_max)
            s_status.transform_us_max = transform_us;
        if (err == ESP_OK) {
            s_status.blocks++;
            s_status.holdover_blocks = 0u;
            s_status.state = C5VRX_RAW_AV_LIVE;
        } else {
            s_status.state = C5VRX_RAW_AV_HOLDOVER;
            s_status.holdover_blocks++;
        }
        taskEXIT_CRITICAL(&s_lock);
    }

finished:
    free(iq); free(phase); free(cvbs);
    /* Producer failure must restore legal PAL without waiting for USB. */
    (void)c5vrx_cvbs_live_out_stop();
    c5vrx_wbfm_hw_destroy(s_wbfm);
    s_wbfm = NULL;
    (void)c5vrx_cvbs_output_resume();
    set_state(C5VRX_RAW_AV_NO_RF);
    s_task = NULL;
    vTaskDelete(NULL);
}

esp_err_t c5vrx_raw_av_start(void)
{
    if (s_task) return ESP_ERR_INVALID_STATE;
    esp_err_t err = c5vrx_raw_av_analyze();
    if (err != ESP_OK || !s_status.live_ready)
        return err == ESP_OK ? ESP_ERR_NOT_FOUND : err;

    err = c5vrx_wbfm_hw_create_kernel(
        C5VRX_RF_BLOCK_WORDS, C5VRX_WBFM_PHASE8_4TO1, &s_wbfm);
    if (err == ESP_OK) err = c5vrx_wbfm_hw_set_dc(
        s_wbfm, s_status.video.i_dc, s_status.video.q_dc);
    if (err != ESP_OK) goto fail;
    err = c5vrx_cvbs_output_suspend();
    if (err != ESP_OK) goto fail;
    err = c5vrx_cvbs_live_out_start_at_rate(
        C5VRX_CVBS_BLOCK_SAMPLES, C5VRX_RAW_CVBS_RATE_HZ);
    if (err != ESP_OK) {
        (void)c5vrx_cvbs_output_resume();
        goto fail;
    }
    c5vrx_cvbs_live_out_set_boundary_hold(true, C5VRX_DAC_BLANK_CODE);
    s_stop = false;
    memset(&s_status.blocks, 0,
           offsetof(c5vrx_raw_av_status_t, live_ready) -
           offsetof(c5vrx_raw_av_status_t, blocks));
    s_status.live_ready = true;
    set_state(C5VRX_RAW_AV_HOLDOVER);
    if (xTaskCreate(raw_av_task, "c5vrx_raw_av", 4096, NULL, 19,
                    &s_task) != pdPASS) {
        err = ESP_ERR_NO_MEM;
        (void)c5vrx_cvbs_live_out_stop();
        (void)c5vrx_cvbs_output_resume();
        goto fail;
    }
    c5vrx_raw_av_print_status();
    return ESP_OK;
fail:
    c5vrx_wbfm_hw_destroy(s_wbfm);
    s_wbfm = NULL;
    return err;
}

esp_err_t c5vrx_raw_av_stop(void)
{
    s_stop = true;
    for (unsigned i = 0; i < 1000u && s_task; ++i)
        vTaskDelay(pdMS_TO_TICKS(1));
    if (s_task) return ESP_ERR_TIMEOUT;
    const esp_err_t output_err = c5vrx_cvbs_live_out_stop();
    c5vrx_wbfm_hw_destroy(s_wbfm);
    s_wbfm = NULL;
    const esp_err_t fallback_err = c5vrx_cvbs_output_resume();
    set_state(C5VRX_RAW_AV_NO_RF);
    return output_err == ESP_OK ? fallback_err : output_err;
}

bool c5vrx_raw_av_running(void)
{
    return s_task != NULL;
}

void c5vrx_raw_av_get_status(c5vrx_raw_av_status_t *status)
{
    if (!status) return;
    taskENTER_CRITICAL(&s_lock);
    *status = s_status;
    taskEXIT_CRITICAL(&s_lock);
}

const char *c5vrx_raw_av_state_name(c5vrx_raw_av_state_t state)
{
    switch (state) {
        case C5VRX_RAW_AV_NO_RF: return "NO_RF";
        case C5VRX_RAW_AV_ACQUIRE: return "ACQUIRE";
        case C5VRX_RAW_AV_LIVE: return "LIVE";
        case C5VRX_RAW_AV_HOLDOVER: return "HOLDOVER";
        default: return "UNKNOWN";
    }
}

void c5vrx_raw_av_print_analysis(void)
{
    c5vrx_raw_av_status_t s;
    c5vrx_raw_av_get_status(&s);
    printf("C5VRX_IQ_VIDEO_ANALYSIS valid_iq=%u i_dc=%ld q_dc=%ld clipping=%u phase_coherence=%u pal_score=%u ntsc_score=%u polarity=%c polarity_confidence=%u samples_per_line_q16=%u native_sample_time=%u sample_time_confidence=%u sync_level=%ld blank_level=%ld burst_detected=%u burst_frequency=%u recommended_wbfm_gain=%ld recommended_wbfm_bias=%ld classification=%s\n",
           (unsigned)s.video.valid_iq, (long)s.video.i_dc, (long)s.video.q_dc,
           (unsigned)s.video.clipping, (unsigned)s.video.phase_coherence_ppm,
           (unsigned)s.video.pal_score, (unsigned)s.video.ntsc_score,
           s.video.polarity > 0 ? '+' : '-',
           (unsigned)s.video.polarity_confidence,
           (unsigned)s.video.samples_per_line_q16,
           (unsigned)s.video.native_sample_time_hz,
           (unsigned)s.video.sample_time_confidence, (long)s.video.sync_level,
           (long)s.video.blank_level, (unsigned)s.video.burst_detected,
           (unsigned)s.video.burst_frequency_hz,
           (long)s.video.recommended_gain_q8,
           (long)s.video.recommended_bias_q8,
           c5vrx_iq_video_classification_name(s.video.classification));
}

void c5vrx_raw_av_print_status(void)
{
    c5vrx_raw_av_status_t s;
    c5vrx_raw_av_get_status(&s);
    const uint64_t gaps = s.blocks > 1u ? s.blocks - 1u : 0u;
    printf("C5VRX_RAW_AV rf=A1/5865 producer=FINITE_VENDOR_BLOCK iq_time_hz=%u wbfm=PHASE8_4TO1 cvbs_hz=%u standard=%s polarity=%c iq_dc_i=%ld iq_dc_q=%ld sync_raw=%ld blank_raw=%ld phase_gain=%ld phase_bias=%ld dac_sync=%u dac_blank=%u dac_white=%u gap_avg_ns=%llu gap_max_ns=%u gap_bridge=LOCAL_HOLD buffer_bytes=%u transform_us_avg=%llu transform_us_max=%u blocks=%llu state=%s live_ready=%u\n",
           C5VRX_RF_IQ_TIMEBASE_HZ, C5VRX_RAW_CVBS_RATE_HZ,
           c5vrx_iq_video_classification_name(s.video.classification),
           s.video.polarity > 0 ? '+' : '-', (long)s.video.i_dc,
           (long)s.video.q_dc, (long)s.video.sync_level,
           (long)s.video.blank_level, (long)s.video.recommended_gain_q8,
           (long)s.video.recommended_bias_q8, C5VRX_DAC_SYNC_CODE,
           C5VRX_DAC_BLANK_CODE, C5VRX_DAC_WHITE_CODE,
           (unsigned long long)(gaps ? s.gap_ns_total / gaps : 0u),
           (unsigned)s.gap_ns_max,
           C5VRX_CVBS_BLOCK_SAMPLES * 3u,
           (unsigned long long)(s.blocks ?
               s.transform_us_total / s.blocks : 0u),
           (unsigned)s.transform_us_max, (unsigned long long)s.blocks,
           c5vrx_raw_av_state_name(s.state),
           (unsigned)s.live_ready);
}
