/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Fixed-capacity byte staging ring for shallow best-effort side taps.
 * Capacity must be a power of two. The ring itself is unsynchronized:
 * on single-core FreeRTOS the owner wraps every access in one portMUX
 * critical section, keeping producer and consumer critical paths to two
 * bounded memcpys each. Overflow drops newest bytes and counts them so
 * latency can never grow silently. */
typedef struct {
    uint8_t *storage;
    uint32_t capacity;
    uint32_t mask;
    uint32_t head;
    uint32_t tail;
    uint32_t dropped_bytes;
    uint32_t peak_bytes;
} c5vrx_sample_ring_t;

bool c5vrx_sample_ring_init(c5vrx_sample_ring_t *ring, void *storage,
                            uint32_t capacity);

/* Accepts as many bytes as fit; a non-zero remainder is counted as
 * dropped. Returns accepted byte count. */
size_t c5vrx_sample_ring_write(c5vrx_sample_ring_t *ring, const uint8_t *data,
                               size_t count);

/* Removes up to limit bytes into out, preserving order across wraps. */
size_t c5vrx_sample_ring_read(c5vrx_sample_ring_t *ring, uint8_t *out,
                              size_t limit);

uint32_t c5vrx_sample_ring_used(const c5vrx_sample_ring_t *ring);
uint32_t c5vrx_sample_ring_free(const c5vrx_sample_ring_t *ring);
