#include "c5vrx_control.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "c5vrx_adc_dump.h"
#include "c5vrx_phy_hacks.h"
#include "c5vrx_wifi5.h"

typedef struct {
    c5vrx_band_t band;
    uint8_t channel;
    bool ht40;
    bool direct_tune_enabled;
    bool started;
} c5vrx_control_state_t;

static c5vrx_control_state_t s_state;

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

    printf("C5VRX_STATUS band=%c channel=%u mhz=%u wifi=%u center=%u offset=%d exact=%d inside=%d bw=%u readback=%u direct=%d\n",
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
           s_state.direct_tune_enabled ? 1 : 0);
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
        printf("C5VRX_HELP commands=PING,STATUS,SET_<band>_<1-8>,BW_<20|40>,CAPTURE_<256-16384>\n");
        fflush(stdout);
        return;
    }
    if (strcasecmp(line, "STATUS") == 0) {
        print_status();
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

    printf("C5VRX_READY protocol=1\n");
    printf("C5VRX_HELP commands=PING,STATUS,SET_<band>_<1-8>,BW_<20|40>,CAPTURE_<256-16384>\n");
    fflush(stdout);

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
