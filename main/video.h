#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

/**
 * video_start() - Initialize and start the PARLIO RX+TX realtime pipeline.
 *
 * Sets up:
 *   - PARLIO RX @ 40 MHz, POS edge, 8-bit, 16 KiB cyclic DMA ring
 *   - PARLIO TX @ 40 MHz, [D,D] output, loop_transmission
 *   - TX BitScrambler with embedded Phase5 LUT (fm.bsasm)
 *   - One-time RX start, then TX starts after one-block delay
 *
 * After video_start() returns ESP_OK, the CPU is done.
 * The hardware pipeline runs forever without any software involvement.
 *
 * NO periodic tasks, NO telemetry, NO calibration loading.
 */
esp_err_t video_start(void);

/* Raw ring size (bytes = 40 MS/s samples). */
#define VIDEO_RX_RING_BYTES 16384u

/**
 * video_copy_recent_rx() - Copy the RX descriptors the GDMA has completed,
 * oldest first, into dst (time-contiguous, ~300 us of I/Q). Waits for the
 * next descriptor boundary first so the oldest block has a full descriptor
 * time before it is overwritten. Returns the bytes copied (0 on failure).
 * Meant for CONFIG_C5VRX_LINK_MODE, where no TX GDMA reads the ring.
 */
size_t video_copy_recent_rx(uint8_t *dst, size_t max_bytes);

/** The raw RX ring (VIDEO_RX_RING_BYTES, one 40 MS/s sample per byte). */
const uint8_t *video_rx_ring(void);

/** Ring offset of the descriptor the RX GDMA is writing now: every byte
 * before it (cyclically) is complete. False if the pointer is unknown. */
bool video_rx_write_offset(uint32_t *offset);

/** Largest RX descriptor, bytes (how far the GDMA may be ahead of the
 * write offset). */
uint32_t video_rx_max_descriptor(void);

/** RF gain index in use, and a fixed gain for link mode: the receiver's
 * automatic gain control is put into manual mode, so the gain only changes
 * when the caller (the link, between two frames) says so. */
uint8_t video_rx_gain(void);
void video_set_rx_gain(uint8_t gain);
