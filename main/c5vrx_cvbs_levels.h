/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Structure-derived composite level recovery (issue #5 section 5).
 *
 * Slow continuous estimators keyed to real waveform structure instead of
 * per-block percentile normalization:
 *  - sync tip via minimum envelope during sync pulses;
 *  - blanking / back porch via minimum envelope of the porch region,
 *    so burst energy cannot contaminate the estimate;
 *  - white reference via a slowly decaying active-video upper envelope.
 *
 * Recommendations feed the conditioner's external-bias mode: bias pins
 * measured blanking at the blank target, one uniform gain maps the
 * measured blanking->white span onto the output codes, preserving
 * relative luma/chroma amplitude by construction. Updates are smooth by
 * the same smoothing exponent - never fast AGC pumping. */

typedef enum {
    C5VRX_LEVEL_REGION_OTHER = 0,
    C5VRX_LEVEL_REGION_SYNC_TIP,
    C5VRX_LEVEL_REGION_BACK_PORCH,
    C5VRX_LEVEL_REGION_ACTIVE,
} c5vrx_level_region_t;

typedef struct {
    uint8_t blank_target;   /* Output code for blanking. Default 19. */
    uint8_t white_target;   /* Default 63. */
    unsigned shift;         /* Smoothing exponent; higher = slower. */
} c5vrx_cvbs_levels_config_t;

typedef struct {
    c5vrx_cvbs_levels_config_t cfg;
    int32_t sync_tip_q8;
    int32_t blanking_q8;
    int32_t white_envelope_q8;
    uint32_t active_samples;
    bool primed;
    bool recommend_latched;
    int out_bias_q8;
    int out_gain_q8;
    uint32_t sync_updates;
    uint32_t porch_updates;
    uint32_t clipping_events;   /* Active samples at/near code 63. */
} c5vrx_cvbs_levels_t;

void c5vrx_cvbs_levels_init(c5vrx_cvbs_levels_t *levels,
                            const c5vrx_cvbs_levels_config_t *config);

void c5vrx_cvbs_levels_observe(c5vrx_cvbs_levels_t *levels,
                               uint8_t sample,
                               c5vrx_level_region_t region);

/* Smooth recommendation for the conditioner's external-bias mode:
 * bias places measured blanking at blank_target, gain maps the
 * measured blanking->white span onto blank..white targets. Output
 * slews by a bounded step per call so updates are always smooth; on
 * the first call after priming the outputs latch directly to target
 * instead of slewing from the idle defaults. */
void c5vrx_cvbs_levels_recommend(c5vrx_cvbs_levels_t *levels,
                                 int *bias,
                                 int *gain);

#ifdef __cplusplus
}
#endif
