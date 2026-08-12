#include <inttypes.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "esp_chip_info.h"
#include "esp_log.h"

#include "c5vrx_channels.h"
#include "c5vrx_rf.h"

static const char *TAG = "C5VRX";

#define C5VRX_EXPERIMENTAL_DIRECT_TUNE 0

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

    c5vrx_print_fpv_coverage();

    c5vrx_fpv_channel_t target;
    c5vrx_frequency_plan_t plan;
    ESP_ERROR_CHECK(c5vrx_get_fpv_channel(C5VRX_BAND_R, 5, &target) ? ESP_OK : ESP_FAIL);
    ESP_ERROR_CHECK(c5vrx_plan_frequency(target.mhz, &plan) ? ESP_OK : ESP_FAIL);

    ESP_LOGW(TAG,
             "Target %c%u / %s: %u MHz; bootstrap Wi-Fi ch%u=%u MHz, offset=%+d MHz",
             target.letter, target.channel, target.name, target.mhz,
             plan.wifi_channel, plan.wifi_center_mhz, plan.offset_mhz);

    const c5vrx_rx_config_t cfg = {
        .wifi_channel = plan.wifi_channel,
        .center_mhz = target.mhz,
        .ht40 = true,
        .try_direct_frequency = C5VRX_EXPERIMENTAL_DIRECT_TUNE,
        .rate = PHY_RATE_6M,
    };

    ESP_ERROR_CHECK(c5vrx_rf_init());
    ESP_ERROR_CHECK(c5vrx_rf_start(&cfg));

    ESP_LOGW(TAG,
             "Packet counters below are NOT analog video. Next milestone: FE/baseband dump capture before 802.11 decode.");

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
