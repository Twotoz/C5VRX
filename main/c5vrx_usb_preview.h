/* SPDX-License-Identifier: GPL-3.0-only */

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
    uint32_t staged_dropped_samples;
    uint16_t staged_peak_bytes;
} c5vrx_usb_preview_stats_t;

esp_err_t c5vrx_usb_preview_start(void);
esp_err_t c5vrx_usb_preview_stop(void);
bool c5vrx_usb_preview_running(void);

/* Best-effort side-tap entry for the AV hot path: never blocks, never
 * grows latency. When the preview is not running this costs one flag
 * check; when it runs but the staging ring is full the newest samples
 * are dropped and counted instead of delaying the caller. */
void c5vrx_usb_preview_submit(const uint8_t *cvbs, size_t samples);

/* Synchronous ingest kept for synthetic benchmarks outside LIVE. */
void c5vrx_usb_preview_ingest(const uint8_t *cvbs, size_t samples);
void c5vrx_usb_preview_get_stats(c5vrx_usb_preview_stats_t *stats);
