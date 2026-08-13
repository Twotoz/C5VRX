#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#define C5VRX_USB_PREVIEW_WIDTH 160u
#define C5VRX_USB_PREVIEW_HEIGHT 120u

esp_err_t c5vrx_usb_preview_start(void);
esp_err_t c5vrx_usb_preview_stop(void);
bool c5vrx_usb_preview_running(void);
void c5vrx_usb_preview_ingest(const uint8_t *cvbs, size_t samples);
