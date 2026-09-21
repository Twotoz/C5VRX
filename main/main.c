/**
 * main.c - C5VRX-3 application entry point.
 *
 * Starts the RF frontend, runs the bounded TRUE80 receive oracle, then starts
 * the proven live video pipeline. TRUE80 is diagnostic-only until sustained
 * realtime 80->40 DSP throughput is physically proven.
 */

#include "rf.h"
#include "video.h"
#include "true80_lab.h"

#include "esp_err.h"
#include "esp_log.h"

static const char *TAG = "c5vrx3_main";

void app_main(void)
{
    ESP_ERROR_CHECK(rf_start());

    /* Experimental PR hardware oracle. Never block normal flight video when
     * the board cannot expose/capture the native MODEM 80 MHz clock. */
    esp_err_t true80_err = true80_lab_boot_probe();
    if (true80_err != ESP_OK) {
        ESP_LOGW(TAG, "TRUE80 oracle incomplete (%s); continuing with proven 40 MS/s live path",
                 esp_err_to_name(true80_err));
    }

    ESP_ERROR_CHECK(video_start());
    /* Hardware pipeline is running. Application has nothing more to do. */
}
