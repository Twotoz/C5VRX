/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_wifi5.h"

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"

static const char *TAG = "c5vrx_wifi5";
static bool s_initialized;
static bool s_started;
static bool s_promiscuous;
static uint16_t s_requested_channel;
static bool s_ht40;

esp_err_t c5vrx_wifi5_init(void)
{
    if (s_initialized) {
        return ESP_OK;
    }

    esp_err_t err = esp_netif_init();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }

    err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    err = esp_wifi_init(&cfg);
    if (err != ESP_OK) {
        return err;
    }

    err = esp_wifi_set_storage(WIFI_STORAGE_RAM);
    if (err != ESP_OK) {
        return err;
    }
    err = esp_wifi_set_mode(WIFI_MODE_STA);
    if (err != ESP_OK) {
        return err;
    }
    err = esp_wifi_start();
    if (err != ESP_OK) {
        return err;
    }
    s_started = true;

#if CONFIG_SOC_WIFI_SUPPORT_5G
    err = esp_wifi_set_band_mode(WIFI_BAND_MODE_5G_ONLY);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to select 5 GHz-only Wi-Fi band: %s", esp_err_to_name(err));
        return err;
    }
#else
    ESP_LOGE(TAG, "This target does not advertise CONFIG_SOC_WIFI_SUPPORT_5G");
    return ESP_ERR_NOT_SUPPORTED;
#endif

    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_set_ps(WIFI_PS_NONE));

    s_initialized = true;
    ESP_LOGI(TAG, "Public ESP-IDF Wi-Fi driver initialized in 5 GHz-only mode");
    return ESP_OK;
}

esp_err_t c5vrx_wifi5_start(uint16_t channel, bool ht40)
{
    esp_err_t err = c5vrx_wifi5_init();
    if (err != ESP_OK) {
        return err;
    }
    if (channel > UINT8_MAX) {
        return ESP_ERR_INVALID_ARG;
    }

    err = esp_wifi_set_bandwidth(WIFI_IF_STA, ht40 ? WIFI_BW40 : WIFI_BW20);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set %s bandwidth: %s", ht40 ? "40 MHz" : "20 MHz", esp_err_to_name(err));
        return err;
    }

    err = esp_wifi_set_channel((uint8_t)channel, WIFI_SECOND_CHAN_NONE);
    if (err != ESP_OK) {
        ESP_LOGE(TAG,
                 "Failed to tune standard Wi-Fi channel %u: %s. The public driver enforces its configured regulatory domain.",
                 channel, esp_err_to_name(err));
        return err;
    }

    err = esp_wifi_set_promiscuous(true);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to enable promiscuous receive: %s", esp_err_to_name(err));
        return err;
    }

    s_requested_channel = channel;
    s_ht40 = ht40;
    s_promiscuous = true;

    uint8_t primary = 0;
    wifi_second_chan_t secondary = WIFI_SECOND_CHAN_NONE;
    err = esp_wifi_get_channel(&primary, &secondary);
    if (err == ESP_OK) {
        ESP_LOGW(TAG,
                 "5 GHz public-driver RX active: requested ch%u, readback ch%u, BW=%s, promiscuous=1",
                 channel, primary, ht40 ? "40 MHz" : "20 MHz");
    }
    return err;
}

esp_err_t c5vrx_wifi5_get_status(c5vrx_wifi5_status_t *out)
{
    if (!out) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_initialized || !s_started) {
        return ESP_ERR_INVALID_STATE;
    }

    uint8_t primary = 0;
    wifi_second_chan_t secondary = WIFI_SECOND_CHAN_NONE;
    const esp_err_t err = esp_wifi_get_channel(&primary, &secondary);
    if (err != ESP_OK) {
        return err;
    }

    *out = (c5vrx_wifi5_status_t) {
        .requested_channel = s_requested_channel,
        .active_primary_channel = primary,
        .ht40 = s_ht40,
        .promiscuous_enabled = s_promiscuous,
    };
    return ESP_OK;
}

void c5vrx_wifi5_stop(void)
{
    if (!s_initialized) {
        return;
    }
    if (s_promiscuous) {
        esp_wifi_set_promiscuous(false);
        s_promiscuous = false;
    }
    if (s_started) {
        esp_wifi_stop();
        s_started = false;
    }
    esp_wifi_deinit();
    s_initialized = false;
}
