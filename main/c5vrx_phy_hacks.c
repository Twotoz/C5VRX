#include "c5vrx_phy_hacks.h"

#include "esp_log.h"
#include "c5vrx_channels.h"

static const char *TAG = "c5vrx_phy_hacks";

/*
 * Reverse-engineered call shape used independently by esp-hosted-open.
 * Still undocumented by Espressif: keep this weak and opt-in.
 */
extern void phy_set_freq(int freq_mhz) __attribute__((weak));

bool c5vrx_phy_has_direct_frequency_hook(void)
{
    return phy_set_freq != NULL;
}

esp_err_t c5vrx_phy_set_frequency_mhz(uint16_t mhz)
{
    if (!c5vrx_frequency_in_c5_rx_window(mhz)) {
        ESP_LOGE(TAG, "Refusing %u MHz: outside C5 specified RX window %u-%u MHz",
                 mhz, C5VRX_C5_RX_MIN_MHZ, C5VRX_C5_RX_MAX_MHZ);
        return ESP_ERR_INVALID_ARG;
    }
    if (!phy_set_freq) {
        ESP_LOGW(TAG, "Undocumented phy_set_freq hook is unavailable in the linked PHY blob");
        return ESP_ERR_NOT_SUPPORTED;
    }

    ESP_LOGW(TAG, "EXPERIMENTAL undocumented PHY retune -> %u MHz", mhz);
    phy_set_freq((int)mhz);
    return ESP_OK;
}
