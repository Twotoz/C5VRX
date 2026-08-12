#include "c5vrx_rf.h"

#include "esp_log.h"
#include "c5vrx_channels.h"

static const char *TAG = "c5vrx_rf";
static bool s_initialized;
static bool s_running;

extern void phy_set_freq(int freq_mhz) __attribute__((weak));

uint16_t c5vrx_wifi5_channel_to_mhz(uint16_t channel)
{
    return (uint16_t)(5000U + 5U * channel);
}

bool c5vrx_rf_has_direct_frequency_hook(void)
{
    return phy_set_freq != NULL;
}

esp_err_t c5vrx_rf_init(void)
{
    if (s_initialized) {
        return ESP_OK;
    }

    esp_wifi_power_domain_on();
    esp_phy_rftest_config(1);
    esp_phy_rftest_init();

    s_initialized = true;
    ESP_LOGI(TAG, "RF certification/test PHY initialized");
    ESP_LOGI(TAG, "direct-frequency hook phy_set_freq: %s",
             c5vrx_rf_has_direct_frequency_hook() ? "present" : "not linked");
    return ESP_OK;
}

esp_err_t c5vrx_rf_set_frequency_mhz(uint16_t mhz)
{
    if (!s_initialized) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!c5vrx_frequency_in_c5_rx_window(mhz)) {
        ESP_LOGE(TAG, "Refusing %u MHz: outside C5 specified RX window %u-%u MHz",
                 mhz, C5VRX_C5_RX_MIN_MHZ, C5VRX_C5_RX_MAX_MHZ);
        return ESP_ERR_INVALID_ARG;
    }
    if (!phy_set_freq) {
        ESP_LOGW(TAG, "phy_set_freq is unavailable in the linked PHY library");
        return ESP_ERR_NOT_SUPPORTED;
    }

    ESP_LOGW(TAG, "EXPERIMENTAL direct PHY retune -> %u MHz", mhz);
    phy_set_freq((int)mhz);
    return ESP_OK;
}

esp_err_t c5vrx_rf_start(const c5vrx_rx_config_t *cfg)
{
    if (!cfg) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_initialized) {
        esp_err_t err = c5vrx_rf_init();
        if (err != ESP_OK) {
            return err;
        }
    }

    if (s_running) {
        c5vrx_rf_stop();
    }

    const uint16_t computed = c5vrx_wifi5_channel_to_mhz(cfg->wifi_channel);
    ESP_LOGW(TAG,
             "Starting experimental C5 RF-test RX: ch%u (~%u MHz), target=%u MHz, HT40=%d, rate=0x%x",
             cfg->wifi_channel, computed, cfg->center_mhz, cfg->ht40, (unsigned)cfg->rate);

    esp_phy_test_start_stop(3);
    esp_phy_cbw40m_en(cfg->ht40);
    esp_phy_wifi_rx(cfg->wifi_channel, cfg->rate);
    s_running = true;

    if (cfg->try_direct_frequency && cfg->center_mhz && cfg->center_mhz != computed) {
        const esp_err_t tune_err = c5vrx_rf_set_frequency_mhz(cfg->center_mhz);
        if (tune_err != ESP_OK) {
            ESP_LOGW(TAG,
                     "Direct retune failed (%s); RX remains on nearest Wi-Fi center %u MHz",
                     esp_err_to_name(tune_err), computed);
        }
    }

    return ESP_OK;
}

void c5vrx_rf_stop(void)
{
    if (!s_initialized) {
        return;
    }
    esp_phy_test_start_stop(0);
    s_running = false;
    ESP_LOGI(TAG, "RF-test RX stopped");
}

void c5vrx_rf_get_result(esp_phy_rx_result_t *out)
{
    if (!out) {
        return;
    }
    esp_phy_get_rx_result(out);
}
