/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Residual fractional clock bridge (issue #5 section 4).
 *
 * Corrects ONLY the small source/output sample-rate mismatch left after
 * hardware PARLIO clock selection. Bounded elastic FIFO with fractional-
 * phase reads; interpolation is selectable between 2-tap linear and a
 * 4-tap Catmull-Rom cubic so burst phase fidelity can be A/B measured on
 * hardware instead of assumed. Discrete insert/drop corrections are only
 * ever applied when the caller declares a safe blanking/vertical
 * boundary, never in active video or burst.
 *
 * The bridge never becomes a line/frame reconstruction stage: it is a
 * per-sample rate adapter over a shallow preallocated FIFO. */

#define C5VRX_BRIDGE_LINEAR 0u
#define C5VRX_BRIDGE_CUBIC 1u

typedef struct {
    uint8_t *storage;        /* Caller-provided, capacity bytes. */
    uint32_t capacity;       /* Power of two. */
    uint32_t input_rate;
    uint32_t output_rate;
    uint8_t method;          /* C5VRX_BRIDGE_LINEAR or _CUBIC. */
} c5vrx_clock_bridge_config_t;

typedef struct {
    c5vrx_clock_bridge_config_t cfg;
    uint32_t head;
    uint32_t tail;
    uint64_t read_pos;       /* Absolute integer read position. */
    uint64_t phase_q32;      /* Fractional offset within read position. */
    bool primed;
    /* Telemetry. */
    uint64_t inserted_samples;
    uint64_t dropped_samples;
    uint64_t underflows;
    uint32_t occupancy_min;
    uint32_t occupancy_max;
} c5vrx_clock_bridge_t;

bool c5vrx_clock_bridge_init(c5vrx_clock_bridge_t *bridge,
                             const c5vrx_clock_bridge_config_t *config);

/* Push raw source samples; at_safe_boundary marks that a discrete
 * correction (insert/drop) may happen right now if occupancy demands it. */
void c5vrx_clock_bridge_push(c5vrx_clock_bridge_t *bridge,
                             const uint8_t *samples,
                             size_t count,
                             bool at_safe_boundary);

/* Pull up to limit resampled output samples; returns produced count. */
size_t c5vrx_clock_bridge_pull(c5vrx_clock_bridge_t *bridge,
                               uint8_t *out,
                               size_t limit);

uint32_t c5vrx_clock_bridge_occupancy(const c5vrx_clock_bridge_t *bridge);

#ifdef __cplusplus
}
#endif
