#include <inttypes.h>

#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "esp_chip_info.h"
#include "esp_log.h"

#include "c5vrx_adc_dump.h"
#include "c5vrx_channels.h"
#include "c5vrx_control.h"
#include "c5vrx_cvbs_out.h"
#include "c5vrx_phy_hacks.h"
#include "c5vrx_rf.h"
#include "c5vrx_wifi5.h"

static const char *TAG = "C5VRX";

#if CONFIG_C5VRX_RX_HT40
#define C5VRX_CFG_HT40 true
#else
#define C5VRX_CFG_HT40 false
#endif

#if CONFIG_C5VRX_EXPERIMENTAL_DIRECT_TUNE
#define C5VRX_CFG_DIRECT_TUNE true
#else
#define C5VRX_CFG_DIRECT_TUNE false
#endif

#if CONFIG_C5VRX_ADC_DUMP_PRINT_RAW
#define C5VRX_CFG_ADC_PRINT_RAW true
#else
#define C5VRX_CFG_ADC_PRINT_RAW false
#endif

static c5vrx_band_t configured_band(void)
{
#if CONFIG_C5VRX_TARGET_BAND_A
    return C5VRX_BAND_A;
#elif CONFIG_C5VRX_TARGET_BAND_B
    return C5VRX_BAND_B;
#elif CONFIG_C5VRX_TARGET_BAND_E
    return C5VRX_BAND_E;
#elif CONFIG_C5VRX_TARGET_BAND_F
    return C5VRX_BAND_F;
#else
    return C5VRX_BAND_R;
#endif
}

static void maybe_run_adc_dump(const c5vrx_frequency_plan_t *plan)
{
#if CONFIG_C5VRX_EXPERIMENTAL_ADC_DUMP
    if (!plan->exact_wifi_center && !C5VRX_CFG_DIRECT_TUNE) {
        ESP_LOGW(TAG,
                 "ADC dump target is offset %+d MHz from the bootstrap center and direct tune is disabled; use an exact channel such as A4/5805 for the first hardware proof",
                 plan->offset_mhz);
    }

    vTaskDelay(pdMS_TO_TICKS(100));
    ESP_ERROR_CHECK(c5vrx_adc_dump_capture(
        CONFIG_C5VRX_ADC_DUMP_SAMPLES,
        C5VRX_CFG_ADC_PRINT_RAW));
#else
    (void)plan;
#endif
}

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

    ESP_LOGI(TAG, "C5VRX analog receiver research firmware");
    ESP_LOGI(TAG, "Goal: RX5808-class 5.8 GHz reception with direct analog CVBS output");
    ESP_LOGI(TAG, "Chip revision: %u", (unsigned)info.revision);

#if CONFIG_C5VRX_CVBS_OUTPUT_ONLY_TEST
    ESP_LOGW(TAG, "Output-only proof mode: RF/Wi-Fi bring-up is intentionally skipped");
    ESP_ERROR_CHECK(c5vrx_cvbs_test_start());
    ESP_LOGI(TAG, "PAL CVBS test is running continuously; reset/power-off to stop this proof image");
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
#endif

    c5vrx_print_fpv_coverage();

    c5vrx_fpv_channel_t target;
    c5vrx_frequency_plan_t plan;
    const c5vrx_band_t band = configured_band();
    const uint8_t channel = CONFIG_C5VRX_TARGET_CHANNEL;

    ESP_ERROR_CHECK(c5vrx_get_fpv_channel(band, channel, &target) ? ESP_OK : ESP_FAIL);
    ESP_ERROR_CHECK(c5vrx_plan_frequency(target.mhz, &plan) ? ESP_OK : ESP_FAIL);

    ESP_LOGW(TAG,
             "Target %c%u / %s: %u MHz; bootstrap Wi-Fi ch%u=%u MHz, offset=%+d MHz",
             target.letter, target.channel, target.name, target.mhz,
             plan.wifi_channel, plan.wifi_center_mhz, plan.offset_mhz);

    if (!plan.inside_c5_rx_window) {
        ESP_LOGE(TAG,
                 "Target %c%u is outside the current ESP32-C5 receive window; select another channel in menuconfig",
                 target.letter, target.channel);
        return;
    }

#if CONFIG_C5VRX_RF_BACKEND_WIFI5
    ESP_LOGI(TAG, "RF backend: public ESP-IDF 5 GHz Wi-Fi driver");
    ESP_ERROR_CHECK(c5vrx_wifi5_start(plan.wifi_channel, C5VRX_CFG_HT40));

    if (C5VRX_CFG_DIRECT_TUNE && !plan.exact_wifi_center) {
        err = c5vrx_phy_set_frequency_mhz(target.mhz);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "Experimental direct tune failed: %s", esp_err_to_name(err));
        }
    }

    maybe_run_adc_dump(&plan);

    ESP_ERROR_CHECK(c5vrx_control_start(
        band,
        channel,
        C5VRX_CFG_HT40,
        C5VRX_CFG_DIRECT_TUNE));

    ESP_LOGI(TAG, "USB control ready: select bands/channels, trigger IQ captures and control the CVBS proof output");
    ESP_LOGW(TAG,
             "Promiscuous Wi-Fi RX proves the 5 GHz RF path is active; live analog FPV still requires continuous FE/baseband sample capture and WBFM demodulation.");

    while (true) {
        c5vrx_wifi5_status_t status;
        if (c5vrx_wifi5_get_status(&status) == ESP_OK) {
            ESP_LOGI(TAG,
                     "wifi5 RX: requested=ch%u readback=ch%u BW=%s promiscuous=%d direct-hook=%s",
                     status.requested_channel,
                     status.active_primary_channel,
                     status.ht40 ? "40" : "20",
                     status.promiscuous_enabled,
                     c5vrx_phy_has_direct_frequency_hook() ? "present" : "absent");
        }
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
#else
    ESP_LOGW(TAG,
             "RF backend: certification/test API. ESP-IDF v6.0.2 documents esp_phy_wifi_rx() channel values as 1-14; 5 GHz channel-number use is intentionally not assumed.");

    const c5vrx_rx_config_t cfg = {
        .wifi_channel = plan.wifi_channel,
        .center_mhz = target.mhz,
        .ht40 = C5VRX_CFG_HT40,
        .try_direct_frequency = C5VRX_CFG_DIRECT_TUNE,
        .rate = PHY_RATE_6M,
    };

    ESP_ERROR_CHECK(c5vrx_rf_init());
    ESP_ERROR_CHECK(c5vrx_rf_start(&cfg));
    maybe_run_adc_dump(&plan);

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
#endif
}
