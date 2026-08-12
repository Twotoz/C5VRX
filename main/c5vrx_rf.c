#include "c5vrx_rf.h"

#include "esp_log.h"

static const char *TAG = "c5vrx_rf";
static bool s_initialized;
static bool s_running;

uint16_t c5vrx_wifi5_channel_to_mhz(uint16_t channel)
{
    // Standard 5 GHz Wi-Fi channel numbering in the range we care about.
    // Example: ch161 -> 5000 + 5*161 = 5805 MHz.
    return (uint16_t)(5000U + 5U * channel);
}

void c5vrx_print_direct_fpv_channels(void)
{
    // Analog FPV Band A centers that coincide exactly with standard 5 GHz
    // Wi-Fi channel centers. A8 (5725 MHz) is deliberately omitted here:
    // reaching it may require direct frequency-programming experiments.
    static const struct {
        const char *fpv;
        uint16_t wifi;
        uint16_t mhz;
    } map[] = {
        {"A7", 149, 5745},
        {"A6", 153, 5765},
        {"A5", 157, 5785},
        {"A4", 161, 5805},
        {"A3", 165, 5825},
        {"A2", 169, 5845},
        {"A1", 173, 5865},
    };

    ESP_LOGI(TAG, "Direct analog-FPV/Wi-Fi center-frequency overlap:");
    for (unsigned i = 0; i < sizeof(map) / sizeof(map[0]); ++i) {
        ESP_LOGI(TAG, "  FPV %s = %u MHz = Wi-Fi ch%u", map[i].fpv, map[i].mhz, map[i].wifi);
    }
}

esp_err_t c5vrx_rf_init(void)
{
    if (s_initialized) {
        return ESP_OK;
    }

    // This is the same initialization path used by Espressif's public
    // examples/phy/cert_test example when CONFIG_ESP_PHY_ENABLE_CERT_TEST=y.
    esp_wifi_power_domain_on();
    esp_phy_rftest_config(1);
    esp_phy_rftest_init();

    s_initialized = true;
    ESP_LOGI(TAG, "RF certification/test PHY initialized");
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
             "Starting experimental C5 RF-test RX: ch%u (~%u MHz), HT40=%d, rate=0x%x",
             cfg->wifi_channel, computed, cfg->ht40, (unsigned)cfg->rate);
    if (cfg->center_mhz && computed != cfg->center_mhz) {
        ESP_LOGW(TAG, "Configured center %u MHz differs from channel-derived %u MHz",
                 cfg->center_mhz, computed);
    }

    esp_phy_test_start_stop(3);
    esp_phy_cbw40m_en(cfg->ht40);
    esp_phy_wifi_rx(cfg->wifi_channel, cfg->rate);
    s_running = true;
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
