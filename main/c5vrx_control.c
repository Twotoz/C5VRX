#include "c5vrx_control.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "c5vrx_adc_dump.h"
#include "c5vrx_cvbs_out.h"
#include "c5vrx_phy_hacks.h"
#include "c5vrx_wbfm_hw.h"
#include "c5vrx_live_pipeline.h"
#include "c5vrx_cvbs_live_out.h"
#include "sdkconfig.h"
#include "c5vrx_wifi5.h"

typedef struct {
    c5vrx_band_t band;
    uint8_t channel;
    bool ht40;
    bool direct_tune_enabled;
    bool started;
} c5vrx_control_state_t;

static c5vrx_control_state_t s_state;
static c5vrx_rf_source_t s_finite_source;

#ifdef CONFIG_C5VRX_LIVE_CVBS_INVERT
#define C5VRX_CFG_LIVE_INVERT true
#else
#define C5VRX_CFG_LIVE_INVERT false
#endif

static bool band_from_char(char c, c5vrx_band_t *out)
{
    if (!out) {
        return false;
    }
    switch ((char)toupper((unsigned char)c)) {
        case 'A': *out = C5VRX_BAND_A; return true;
        case 'B': *out = C5VRX_BAND_B; return true;
        case 'E': *out = C5VRX_BAND_E; return true;
        case 'F': *out = C5VRX_BAND_F; return true;
        case 'R': *out = C5VRX_BAND_R; return true;
        default: return false;
    }
}

static void print_status(void)
{
    c5vrx_fpv_channel_t target;
    c5vrx_frequency_plan_t plan;
    c5vrx_wifi5_status_t wifi = {0};

    if (!c5vrx_get_fpv_channel(s_state.band, s_state.channel, &target) ||
        !c5vrx_plan_frequency(target.mhz, &plan)) {
        printf("C5VRX_ERR status-plan\n");
        fflush(stdout);
        return;
    }

    const esp_err_t err = c5vrx_wifi5_get_status(&wifi);
    if (err != ESP_OK) {
        printf("C5VRX_ERR status-wifi code=%d\n", (int)err);
        fflush(stdout);
        return;
    }

    printf("C5VRX_STATUS band=%c channel=%u mhz=%u wifi=%u center=%u offset=%d exact=%d inside=%d bw=%u readback=%u direct=%d cvbs=%d\n",
           target.letter,
           (unsigned)target.channel,
           (unsigned)target.mhz,
           (unsigned)plan.wifi_channel,
           (unsigned)plan.wifi_center_mhz,
           (int)plan.offset_mhz,
           plan.exact_wifi_center ? 1 : 0,
           plan.inside_c5_rx_window ? 1 : 0,
           s_state.ht40 ? 40u : 20u,
           (unsigned)wifi.active_primary_channel,
           s_state.direct_tune_enabled ? 1 : 0,
           c5vrx_cvbs_test_running() ? 1 : 0);
    fflush(stdout);
}

static esp_err_t apply_channel(c5vrx_band_t band, uint8_t channel)
{
    c5vrx_fpv_channel_t target;
    c5vrx_frequency_plan_t plan;

    if (!c5vrx_get_fpv_channel(band, channel, &target)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!c5vrx_plan_frequency(target.mhz, &plan)) {
        return ESP_FAIL;
    }
    if (!plan.inside_c5_rx_window) {
        printf("C5VRX_ERR outside-rx-window band=%c channel=%u mhz=%u max=%u\n",
               target.letter, (unsigned)channel, (unsigned)target.mhz,
               (unsigned)C5VRX_C5_RX_MAX_MHZ);
        fflush(stdout);
        return ESP_ERR_NOT_SUPPORTED;
    }

    esp_err_t err = c5vrx_wifi5_start(plan.wifi_channel, s_state.ht40);
    if (err != ESP_OK) {
        printf("C5VRX_ERR tune-wifi code=%d wifi=%u\n", (int)err, (unsigned)plan.wifi_channel);
        fflush(stdout);
        return err;
    }

    if (s_state.direct_tune_enabled && !plan.exact_wifi_center) {
        err = c5vrx_phy_set_frequency_mhz(target.mhz);
        if (err != ESP_OK) {
            printf("C5VRX_WARN direct-tune-failed code=%d requested=%u\n",
                   (int)err, (unsigned)target.mhz);
        }
    }

    s_state.band = band;
    s_state.channel = channel;

    printf("C5VRX_OK set band=%c channel=%u mhz=%u wifi=%u center=%u offset=%d exact=%d bw=%u\n",
           target.letter,
           (unsigned)target.channel,
           (unsigned)target.mhz,
           (unsigned)plan.wifi_channel,
           (unsigned)plan.wifi_center_mhz,
           (int)plan.offset_mhz,
           plan.exact_wifi_center ? 1 : 0,
           s_state.ht40 ? 40u : 20u);
    fflush(stdout);
    return ESP_OK;
}

static void print_help(void)
{
    printf("C5VRX_HELP commands=PING,STATUS,SET_<band>_<1-8>,BW_<20|40>,CAPTURE_<256-16384>,CHAIN_<2-1024>_<1-16384>,RING_PROBE,WBFM_HWTEST,WBFM_CAPTURE_<8-16384_multiple4>,NEARLIVE_START,NEARLIVE_STOP,PIPELINE_STATS,CVBS_TEST,CVBS_STOP\n");
    fflush(stdout);
}

static void handle_line(char *line)
{
    while (*line && isspace((unsigned char)*line)) {
        ++line;
    }
    char *end = line + strlen(line);
    while (end > line && isspace((unsigned char)end[-1])) {
        *--end = '\0';
    }
    if (!*line) {
        return;
    }

    if (strcasecmp(line, "PING") == 0) {
        printf("C5VRX_PONG\n");
        fflush(stdout);
        return;
    }
    if (strcasecmp(line, "HELP") == 0) {
        print_help();
        return;
    }
    if (strcasecmp(line, "STATUS") == 0) {
        print_status();
        return;
    }
    if (strcasecmp(line, "PIPELINE STATS") == 0 ||
        strcasecmp(line, "PIPELINE_STATS") == 0) {
        c5vrx_live_pipeline_log_stats();
        return;
    }
    if (strcasecmp(line, "NEARLIVE START") == 0 ||
        strcasecmp(line, "NEARLIVE_START") == 0) {
        if (c5vrx_live_pipeline_running() || c5vrx_cvbs_test_running()) {
            printf("C5VRX_ERR nearlive-already-running\n"); fflush(stdout); return;
        }
        esp_err_t err = c5vrx_finite_chain_source_create(&s_finite_source, 16384u);
        if (err == ESP_OK) err = c5vrx_cvbs_live_out_start(4096u);
        const c5vrx_live_pipeline_config_t config = {
            .source = &s_finite_source, .sink = c5vrx_cvbs_live_out_write,
            .maximum_input_words = 16384u,
            .conditioner = {
                .bias_q8 = CONFIG_C5VRX_LIVE_CVBS_BIAS_Q8,
                .gain_q8 = CONFIG_C5VRX_LIVE_CVBS_GAIN_Q8,
                .invert = C5VRX_CFG_LIVE_INVERT,
                .sync_code = 0, .blank_code = 19, .black_code = 20,
                .white_code = 63,
                .clamp_min = CONFIG_C5VRX_LIVE_CVBS_CLAMP_MIN,
                .clamp_max = CONFIG_C5VRX_LIVE_CVBS_CLAMP_MAX,
                .filter_shift = CONFIG_C5VRX_LIVE_CVBS_FILTER_SHIFT,
            },
        };
        if (err == ESP_OK) err = c5vrx_live_pipeline_start(&config);
        if (err != ESP_OK) {
            (void)c5vrx_cvbs_live_out_stop();
            c5vrx_finite_chain_source_destroy(&s_finite_source);
        }
        printf("C5VRX_NEARLIVE_START code=%d mode=FINITE_CHAINED_NOT_CONTINUOUS\n", (int)err);
        fflush(stdout); return;
    }
    if (strcasecmp(line, "NEARLIVE STOP") == 0 ||
        strcasecmp(line, "NEARLIVE_STOP") == 0) {
        esp_err_t err = c5vrx_live_pipeline_stop();
        if (err == ESP_OK) {
            (void)c5vrx_cvbs_live_out_stop();
            c5vrx_finite_chain_source_destroy(&s_finite_source);
        }
        printf("C5VRX_NEARLIVE_STOP code=%d\n", (int)err); fflush(stdout); return;
    }

    if (strcasecmp(line, "WBFM HWTEST") == 0 ||
        strcasecmp(line, "WBFM_HWTEST") == 0) {
        printf("C5VRX_WBFM_HWTEST_BEGIN\n");
        fflush(stdout);
        const esp_err_t err = c5vrx_wbfm_hw_self_test();
        printf("C5VRX_WBFM_HWTEST_DONE code=%d\n", (int)err);
        fflush(stdout);
        return;
    }

    unsigned wbfm_samples = 0;
    if (sscanf(line, "WBFM CAPTURE %u", &wbfm_samples) == 1) {
        if (wbfm_samples < 8 ||
            wbfm_samples > C5VRX_ADC_DUMP_MAX_SAMPLES ||
            (wbfm_samples & 3u) != 0u) {
            printf("C5VRX_ERR invalid-wbfm-capture range=8-%u multiple=4\n",
                   (unsigned)C5VRX_ADC_DUMP_MAX_SAMPLES);
            fflush(stdout);
            return;
        }
        printf("C5VRX_WBFM_CAPTURE_BEGIN iq_samples=%u\n", wbfm_samples);
        fflush(stdout);
        const esp_err_t err = c5vrx_wbfm_hw_probe_dump(wbfm_samples);
        printf("C5VRX_WBFM_CAPTURE_DONE code=%d\n", (int)err);
        fflush(stdout);
        return;
    }

    if (strcasecmp(line, "CVBS TEST") == 0 || strcasecmp(line, "CVBS_TEST") == 0) {
        const esp_err_t err = c5vrx_cvbs_test_start();
        if (err == ESP_OK) {
            printf("C5VRX_OK cvbs-test=1 standard=PAL625 fields_hz=50 frames_hz=25 sample_rate=20000000\n");
        } else {
            printf("C5VRX_ERR cvbs-test code=%d\n", (int)err);
        }
        fflush(stdout);
        return;
    }

    if (strcasecmp(line, "CVBS STOP") == 0 || strcasecmp(line, "CVBS_STOP") == 0) {
        const esp_err_t err = c5vrx_cvbs_test_stop();
        if (err == ESP_OK) {
            printf("C5VRX_OK cvbs-test=0\n");
        } else {
            printf("C5VRX_ERR cvbs-stop code=%d\n", (int)err);
        }
        fflush(stdout);
        return;
    }

    char band_char = 0;
    unsigned channel = 0;
    if (sscanf(line, "SET %c %u", &band_char, &channel) == 2) {
        c5vrx_band_t band;
        if (!band_from_char(band_char, &band) || channel < 1 || channel > 8) {
            printf("C5VRX_ERR invalid-channel\n");
            fflush(stdout);
            return;
        }
        (void)apply_channel(band, (uint8_t)channel);
        return;
    }

    unsigned bw = 0;
    if (sscanf(line, "BW %u", &bw) == 1) {
        if (bw != 20 && bw != 40) {
            printf("C5VRX_ERR invalid-bw allowed=20,40\n");
            fflush(stdout);
            return;
        }
        s_state.ht40 = (bw == 40);
        const esp_err_t err = apply_channel(s_state.band, s_state.channel);
        if (err == ESP_OK) {
            printf("C5VRX_OK bw=%u\n", bw);
            fflush(stdout);
        }
        return;
    }

    unsigned chain_blocks = 0;
    unsigned chain_samples = 0;
    if (sscanf(line, "CHAIN %u %u", &chain_blocks, &chain_samples) == 2) {
        if (chain_blocks < 2 || chain_blocks > 1024 ||
            chain_samples < 1 || chain_samples > C5VRX_ADC_DUMP_MAX_SAMPLES) {
            printf("C5VRX_ERR invalid-chain blocks=2-1024 samples=1-%u\n",
                   (unsigned)C5VRX_ADC_DUMP_MAX_SAMPLES);
            fflush(stdout);
            return;
        }
        c5vrx_adc_chain_stats_t stats = {0};
        printf("C5VRX_CHAIN_BEGIN blocks=%u samples=%u\n", chain_blocks, chain_samples);
        fflush(stdout);
        const esp_err_t err = c5vrx_adc_dump_capture_chained(
            chain_blocks,
            chain_samples,
            &stats);
        printf("C5VRX_CHAIN_DONE code=%d blocks=%u total=%llu repeated_hashes=%u boundary_jump_power=%llu\n",
               (int)err,
               (unsigned)stats.blocks_completed,
               (unsigned long long)stats.total_samples,
               (unsigned)stats.repeated_block_hashes,
               (unsigned long long)stats.boundary_jump_power_sum);
        fflush(stdout);
        return;
    }

    if (strcasecmp(line, "RING PROBE") == 0 ||
        strcasecmp(line, "RING_PROBE") == 0) {
        c5vrx_adc_ring_probe_stats_t stats = {0};
        printf("C5VRX_RING_PROBE_BEGIN mode=SINGLE_ARM_PRETRIGGER_NOT_CONTINUOUS\n");
        fflush(stdout);
        const esp_err_t err = c5vrx_adc_dump_ring_probe(&stats);
        printf("C5VRX_RING_PROBE_DONE code=%d active_us=%llu observations=%u pointer_changes=%u pointer_min=%u pointer_max=%u content_changes=%u done=%u vendor_timeout=%u status=%08x classification=%s\n",
               (int)err, (unsigned long long)stats.active_time_us,
               (unsigned)stats.observations, (unsigned)stats.pointer_changes,
               (unsigned)stats.minimum_pointer, (unsigned)stats.maximum_pointer,
               (unsigned)stats.content_changes, stats.completion_bit_seen ? 1u : 0u,
               stats.reached_vendor_timeout ? 1u : 0u,
               (unsigned)stats.final_status,
               err == ESP_OK && stats.reached_vendor_timeout &&
                       stats.pointer_changes > 2u
                   ? "PRETRIGGER_RING_CANDIDATE_PHYSICAL_VALIDATION_REQUIRED"
                   : "NO_CONTINUOUS_EVIDENCE");
        fflush(stdout);
        return;
    }

    unsigned samples = 0;
    if (sscanf(line, "CAPTURE %u", &samples) == 1) {
        if (samples < 256 || samples > C5VRX_ADC_DUMP_MAX_SAMPLES) {
            printf("C5VRX_ERR invalid-capture range=256-%u\n",
                   (unsigned)C5VRX_ADC_DUMP_MAX_SAMPLES);
            fflush(stdout);
            return;
        }
        printf("C5VRX_CAPTURE_BEGIN samples=%u\n", samples);
        fflush(stdout);
        const esp_err_t err = c5vrx_adc_dump_capture(samples, true);
        printf("C5VRX_CAPTURE_DONE code=%d\n", (int)err);
        fflush(stdout);
        return;
    }

    printf("C5VRX_ERR unknown-command\n");
    fflush(stdout);
}

static void console_task(void *arg)
{
    (void)arg;
    char line[128];

    printf("C5VRX_READY protocol=4\n");
    print_help();

    for (;;) {
        if (fgets(line, sizeof(line), stdin) != NULL) {
            handle_line(line);
        } else {
            clearerr(stdin);
            vTaskDelay(pdMS_TO_TICKS(20));
        }
    }
}

esp_err_t c5vrx_control_start(c5vrx_band_t band,
                              uint8_t channel,
                              bool ht40,
                              bool direct_tune_enabled)
{
    if (s_state.started) {
        return ESP_ERR_INVALID_STATE;
    }
    if (band >= C5VRX_BAND_COUNT || channel < 1 || channel > 8) {
        return ESP_ERR_INVALID_ARG;
    }

    s_state = (c5vrx_control_state_t) {
        .band = band,
        .channel = channel,
        .ht40 = ht40,
        .direct_tune_enabled = direct_tune_enabled,
        .started = true,
    };

    if (xTaskCreate(console_task, "c5vrx_usbctl", 4096, NULL, 5, NULL) != pdPASS) {
        s_state.started = false;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}
