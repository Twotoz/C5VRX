/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

/* Boot-time default rung (Kconfig-overridable); runtime changes go
 * through c5vrx_usb_preview_set_size(). Ladder rungs only - see
 * c5vrx_preview_geometry.h for the bandwidth ceilings per rung. */
#ifndef C5VRX_USB_PREVIEW_DEFAULT_WIDTH
#define C5VRX_USB_PREVIEW_DEFAULT_WIDTH 320u
#endif
#ifndef C5VRX_USB_PREVIEW_DEFAULT_HEIGHT
#define C5VRX_USB_PREVIEW_DEFAULT_HEIGHT 240u
#endif

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
    uint16_t width;
    uint16_t height;
} c5vrx_usb_preview_stats_t;

esp_err_t c5vrx_usb_preview_start(void);
esp_err_t c5vrx_usb_preview_stop(void);
bool c5vrx_usb_preview_running(void);

/* Select a resolution-ladder rung for the next start. Rejected while
 * the preview is running (stop first); non-rung sizes are rejected so
 * host upscaling can never masquerade as transported detail. */
esp_err_t c5vrx_usb_preview_set_size(uint16_t width, uint16_t height);
void c5vrx_usb_preview_get_size(uint16_t *width, uint16_t *height);

/* Best-effort side-tap entry for the AV hot path: never blocks, never
 * grows latency. When the preview is not running this costs one flag
 * check; when it runs but the staging ring is full the newest samples
 * are dropped and counted instead of delaying the caller. */
void c5vrx_usb_preview_submit(const uint8_t *cvbs, size_t samples);

/* Synchronous ingest kept for synthetic benchmarks outside LIVE. */
void c5vrx_usb_preview_ingest(const uint8_t *cvbs, size_t samples);
void c5vrx_usb_preview_get_stats(c5vrx_usb_preview_stats_t *stats);
