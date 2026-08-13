#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#define C5VRX_USB_PREVIEW_WIDTH 160u
#define C5VRX_USB_PREVIEW_HEIGHT 120u
#define C5VRX_USB_PREVIEW_PROTOCOL_VERSION 1u

typedef struct {
    uint32_t samples_ingested;
    uint32_t horizontal_syncs;
    uint32_t vertical_syncs;
    uint32_t rejected_sync_pulses;
    uint32_t lock_acquisitions;
    uint32_t lock_losses;
    uint32_t frames_completed;
    uint32_t frames_sent;
    uint32_t frames_dropped;
    uint32_t line_period_samples;
    uint32_t last_hsync_width;
    uint32_t last_vsync_width;
    uint8_t sync_threshold;
    bool horizontal_locked;
    bool vertical_locked;
} c5vrx_usb_preview_stats_t;

esp_err_t c5vrx_usb_preview_start(void);
esp_err_t c5vrx_usb_preview_stop(void);
bool c5vrx_usb_preview_running(void);
void c5vrx_usb_preview_ingest(const uint8_t *cvbs, size_t samples);
void c5vrx_usb_preview_get_stats(c5vrx_usb_preview_stats_t *stats);
