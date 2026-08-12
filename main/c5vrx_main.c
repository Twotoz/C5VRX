#include <inttypes.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "esp_chip_info.h"
#include "esp_log.h"

#include "c5vrx_rf.h"

static const char *TAG = "C5VRX";

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    esp_chip_info_t info;
    esp_chip_info(&info);

    ESP_LOGI(TAG, "C5VRX research firmware");
    ESP_LOGI(TAG, "Goal: RX5808-class analog FPV reception using the ESP32-C5 5 GHz receive chain");
    ESP_LOGI(TAG, "Chip revision: %u", (unsigned)info.revision);

    c5vrx_print_direct_fpv_channels();

    // First bring-up target: FPV Band A4 = 5805 MHz = Wi-Fi channel 161.
    // HT40 gives the front-end enough receive bandwidth for the first WBFM experiments.
    const c5vrx_rx_config_t cfg = {
        .wifi_channel = 161,
        .center_mhz = 5805,
        .ht40 = true,
        .rate = PHY_RATE_6M,
    };

    ESP_ERROR_CHECK(c5vrx_rf_init());
    ESP_ERROR_CHECK(c5vrx_rf_start(&cfg));

    ESP_LOGW(TAG,
             "Packet counters below are NOT the analog-video output. The next milestone is tapping the FE/baseband dump/IQ path before 802.11 decode.");

    while (true) {
        esp_phy_rx_result_t rx = {0};
        c5vrx_rf_get_result(&rx);
        ESP_LOGI(TAG,
                 "vendor RX: total=%" PRIu32 " desired=%" PRIu32 " rssi=%d flag=%" PRIu32,
                 rx.phy_rx_total_count,
                 rx.phy_rx_correct_count,
                 rx.phy_rx_rssi,
                 rx.phy_rx_result_flag);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
