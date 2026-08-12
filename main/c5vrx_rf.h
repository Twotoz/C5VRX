#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "esp_phy_cert_test.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint16_t wifi_channel;
    uint16_t center_mhz;
    bool ht40;
    esp_phy_wifi_rate_t rate;
} c5vrx_rx_config_t;

/** Initialize Espressif's RF certification/test receive path. */
esp_err_t c5vrx_rf_init(void);

/**
 * Start the vendor RF-test receiver on a Wi-Fi channel.
 *
 * NOTE: The generic esp_phy_cert_test.h comments still describe channels 1-14.
 * ESP32-C5 itself is dual-band and Espressif's C5 RF test tooling supports 5 GHz.
 * Treat 5 GHz use of this low-level API as an experiment until verified on-device.
 */
esp_err_t c5vrx_rf_start(const c5vrx_rx_config_t *cfg);

/** Stop the RF test receiver. */
void c5vrx_rf_stop(void);

/** Read the packet-oriented vendor RX result structure. */
void c5vrx_rf_get_result(esp_phy_rx_result_t *out);

/** Convert standard 5 GHz Wi-Fi channel number to center frequency in MHz. */
uint16_t c5vrx_wifi5_channel_to_mhz(uint16_t channel);

/** Print the FPV Band A channels that line up exactly with normal 5 GHz Wi-Fi centers. */
void c5vrx_print_direct_fpv_channels(void);

#ifdef __cplusplus
}
#endif
