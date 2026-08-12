#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint16_t requested_channel;
    uint8_t active_primary_channel;
    bool ht40;
    bool promiscuous_enabled;
} c5vrx_wifi5_status_t;

/** Initialize the public ESP-IDF Wi-Fi driver for receive-only 5 GHz bring-up. */
esp_err_t c5vrx_wifi5_init(void);

/** Tune the public Wi-Fi driver to a standard 5 GHz Wi-Fi channel. */
esp_err_t c5vrx_wifi5_start(uint16_t channel, bool ht40);

/** Read back the currently active primary Wi-Fi channel. */
esp_err_t c5vrx_wifi5_get_status(c5vrx_wifi5_status_t *out);

/** Disable promiscuous RX and stop/deinit the public Wi-Fi driver. */
void c5vrx_wifi5_stop(void);

#ifdef __cplusplus
}
#endif
