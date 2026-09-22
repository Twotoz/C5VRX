#pragma once

#include <stdint.h>
#include "arc_phy.h"

/*
 * ARC V4 SNAP
 * -----------
 * Calibrated margin handoff for the fast (~6 ms) Q4 observer.
 *
 * The controller does not periodically hunt for gain.  It holds the current
 * vendor-generated gain while Q4 has margin, watches for a real change in the
 * fast/slow raw-Q4 state, and jumps to a hardware-measured gain anchor before
 * the current state reaches the quantizer cliff.
 */

#define ARC_V4_SNAP_ANCHOR_COUNT 6u

typedef enum {
    ARC_V4_SNAP_LOCK = 0,
    ARC_V4_SNAP_PREHANDOFF,
    ARC_V4_SNAP_VERIFY,
    ARC_V4_SNAP_RF_LIMIT,
} arc_v4_snap_state_t;

typedef enum {
    ARC_V4_SNAP_DIR_NONE = 0,
    ARC_V4_SNAP_WEAKER,
    ARC_V4_SNAP_STRONGER,
} arc_v4_snap_direction_t;

typedef enum {
    ARC_V4_SNAP_URGENCY_NONE = 0,
    ARC_V4_SNAP_SOFT,
    ARC_V4_SNAP_FAST,
    ARC_V4_SNAP_CRITICAL,
} arc_v4_snap_urgency_t;

typedef struct {
    int p_median;
    int q_phase;
    int clip_permille;
    int origin_permille;
    int winding_permille;
} arc_v4_snap_observation_t;

typedef struct {
    arc_gain_table_t table;
    uint8_t gain;
    arc_v4_snap_state_t state;

    uint32_t epoch;
    uint32_t handoffs;

    unsigned discard_samples;
    unsigned verify_samples;
    unsigned verify_good_samples;
    unsigned pending_samples;
    unsigned rf_limit_samples;

    arc_v4_snap_direction_t pending_direction;
    arc_v4_snap_urgency_t pending_urgency;

    uint8_t primed;
    int fast_p, slow_p;
    int fast_q, slow_q;
    int fast_clip, slow_clip;
    int fast_origin, slow_origin;
    int fast_winding, slow_winding;

    int margin_score;
    arc_v4_snap_direction_t last_direction;
    arc_v4_snap_urgency_t last_urgency;
} arc_v4_snap_t;

void arc_v4_snap_reset(arc_v4_snap_t *snap,
                       const arc_gain_table_t *table,
                       uint8_t gain);

uint8_t arc_v4_snap_tick(arc_v4_snap_t *snap,
                         const arc_v4_snap_observation_t *o);

const char *arc_v4_snap_state_name(arc_v4_snap_state_t state);
const char *arc_v4_snap_direction_name(arc_v4_snap_direction_t direction);
const char *arc_v4_snap_urgency_name(arc_v4_snap_urgency_t urgency);
