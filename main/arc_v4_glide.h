#pragma once

#include <stdint.h>
#include "arc_phy.h"

/*
 * ARC V4 GLIDE
 * ------------
 * Edge-triggered closed-loop gain slew for the ~6 ms raw-Q4 observer.
 *
 * HOLD is a true zero-write state. Normal RF movement is absorbed by small
 * +2/+4 or -2/-4 vendor-gain steps started before the old quantizer cliff.
 * Large calibrated anchor jumps are reserved for genuine starvation/overload
 * ESCAPE conditions.
 */

#define ARC_V4_GLIDE_ANCHOR_COUNT 6u

typedef enum {
    ARC_V4_GLIDE_HOLD = 0,
    ARC_V4_GLIDE_UP,
    ARC_V4_GLIDE_DOWN,
    ARC_V4_GLIDE_VERIFY,
    ARC_V4_GLIDE_RF_LIMIT,
} arc_v4_glide_state_t;

typedef enum {
    ARC_V4_GLIDE_DIR_NONE = 0,
    ARC_V4_GLIDE_WEAKER,
    ARC_V4_GLIDE_STRONGER,
} arc_v4_glide_direction_t;

typedef enum {
    ARC_V4_GLIDE_ACTION_NONE = 0,
    ARC_V4_GLIDE_ACTION_STEP,
    ARC_V4_GLIDE_ACTION_ESCAPE,
} arc_v4_glide_action_t;

typedef struct {
    int p_median;
    int q_phase;
    int clip_permille;
    int origin_permille;
    int winding_permille;
} arc_v4_glide_observation_t;

typedef struct {
    arc_gain_table_t table;
    uint8_t gain;
    arc_v4_glide_state_t state;

    uint32_t epoch;
    uint32_t writes;
    uint32_t escape_writes;

    unsigned discard_samples;
    unsigned trend_samples;
    unsigned verify_samples;
    unsigned rf_limit_samples;

    uint8_t primed;
    uint8_t baseline_valid;

    int fast_p, slow_p;
    int fast_q, slow_q;
    int fast_clip, slow_clip;
    int fast_origin, slow_origin;
    int fast_winding, slow_winding;

    int base_p;
    int base_q;
    int base_clip;
    int base_origin;
    int base_winding;

    int weak_votes;
    int strong_votes;
    int margin_score;

    arc_v4_glide_direction_t direction;
    arc_v4_glide_action_t last_action;
} arc_v4_glide_t;

void arc_v4_glide_reset(arc_v4_glide_t *glide,
                        const arc_gain_table_t *table,
                        uint8_t gain);

uint8_t arc_v4_glide_tick(arc_v4_glide_t *glide,
                          const arc_v4_glide_observation_t *o);

const char *arc_v4_glide_state_name(arc_v4_glide_state_t state);
const char *arc_v4_glide_direction_name(arc_v4_glide_direction_t direction);
const char *arc_v4_glide_action_name(arc_v4_glide_action_t action);
