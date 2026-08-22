/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Canonical video timing observer (issue #5 section 3).
 *
 * One timing authority for the whole receiver: recovers H phase, field
 * cadence/parity, line identity and the PAL/NTSC standard from the
 * continuous composite waveform itself. Never derives anything from host
 * arrival order, picture brightness or a framebuffer.
 *
 * Composite/FM polarity is part of the canonical state (PR #9 lesson):
 * learned from repeated high-confidence sync evidence, kept stable across
 * ordinary processing blocks, relearned only on a genuine stream
 * discontinuity. Burst/chroma coherence is deliberately NOT an input:
 * color structure must never relocate H-sync into picture content. */

typedef enum {
    C5VRX_VIDEO_STANDARD_UNKNOWN = 0,
    C5VRX_VIDEO_STANDARD_PAL = 1,
    C5VRX_VIDEO_STANDARD_NTSC = 2,
} c5vrx_video_standard_t;

typedef enum {
    C5VRX_COMPOSITE_POLARITY_UNKNOWN = 0,
    /* Sync tips below blanking (normal conditioned CVBS). */
    C5VRX_COMPOSITE_POLARITY_NEGATIVE = 1,
    /* Sync tips above active ceiling (inverted FM path). */
    C5VRX_COMPOSITE_POLARITY_POSITIVE = 2,
} c5vrx_composite_polarity_t;

typedef enum {
    VS_PHASE_ACTIVE = 0,
    VS_PHASE_PRE_EQUALIZING,
    VS_PHASE_SERRATED,
    VS_PHASE_POST_EQUALIZING,
} c5vrx_vs_phase_t;

typedef struct {
    uint32_t minimum_line_samples;   /* Default 900 (20-30 MS/s range). */
    uint32_t maximum_line_samples;   /* Default 1700. */
    unsigned standard_lock_frames;   /* Agreeing frame totals before the
                                      * standard may switch. Default 3;
                                      * benchmark, not sacred. */
    unsigned polarity_lock_votes;    /* Default 3; benchmark, not sacred. */
} c5vrx_video_timing_config_t;

typedef struct {
    uint64_t sample_index;
    bool valid;
    bool horizontal;
    bool field_start;
    bool vertical_interval;
    bool lock_lost;
    uint64_t field_id;
    uint8_t parity;
    uint32_t line_id;
    uint32_t line_period_samples;
    uint32_t sample_phase;
} c5vrx_video_timing_event_t;

typedef struct {
    bool in_pulse;
    uint64_t pulse_start;
    uint32_t pulse_length;
    uint32_t quiet_run;
    uint64_t last_hsync_start;
    bool have_interval;
    unsigned interval_score;
} c5vrx_polarity_engine_t;

typedef struct {
    /* Configuration (resolved defaults filled by init). */
    c5vrx_video_timing_config_t cfg;

    /* Canonical timeline. */
    uint32_t stream_epoch;
    uint64_t field_id;
    uint8_t parity;
    uint32_t line_id;
    uint32_t sample_phase;
    c5vrx_video_standard_t standard;
    c5vrx_composite_polarity_t polarity;
    bool h_locked;
    bool v_locked;

    /* Envelope + pulse engines. */
    uint64_t samples_seen;
    uint8_t sync_floor;
    uint8_t signal_peak;
    c5vrx_polarity_engine_t negative_engine;
    c5vrx_polarity_engine_t positive_engine;

    /* Vertical-interval state machine. */
    c5vrx_vs_phase_t vs_phase;
    uint32_t pre_equalizers;
    uint32_t serrations;
    uint32_t post_equalizers;
    bool in_vertical_interval;

    /* Line/frame accounting. Standard classification and parity
     * validation come from timing relationships only: field duration in
     * samples over the measured line period (PAL 312.5 / NTSC 262.5
     * lines per field), and the alternating full-line/half-line grid
     * phase of each vertical-interval start. Never from H-sync counts
     * alone (the vertical interval itself spans whole lines), picture
     * brightness, or host arrival order. */
    uint32_t line_period_samples;
    int32_t line_period_error_samples;
    uint32_t hsyncs_since_field_start;
    /* Field periods are measured vertical-interval-start to
     * vertical-interval-start: immune to where each interval's
     * completing H-sync happens to sit relative to its block. */
    uint64_t current_vs_start_sample;
    uint64_t last_vs_start_sample;
    uint32_t last_field_duration_samples;
    int8_t vs_phase_class;      /* 0 = full-line aligned, 1 = half-line. */
    int8_t last_vs_phase_class;
    unsigned standard_agreeing_frames;
    c5vrx_video_standard_t standard_candidate;
    uint64_t last_hsync_sample;

    /* Telemetry. */
    uint64_t horizontal_events;
    uint64_t vertical_intervals;
    uint64_t rejected_pulses;
    uint64_t false_vertical_candidates;
    uint64_t parity_slips;
    uint64_t stray_hsync_in_vertical_interval;
    uint64_t lock_acquisitions;
    uint64_t lock_losses;
    uint64_t polarity_votes_negative;
    uint64_t polarity_votes_positive;
    uint64_t opposite_polarity_candidates_rejected;
    uint64_t polarity_relock_events;
} c5vrx_video_timing_state_t;

void c5vrx_video_timing_init(c5vrx_video_timing_state_t *state,
                             const c5vrx_video_timing_config_t *config);

/* Feed one conditioned-composite sample (6-bit code domain). Returns true
 * when a timing event became available. */
bool c5vrx_video_timing_consume(c5vrx_video_timing_state_t *state,
                                uint8_t sample,
                                c5vrx_video_timing_event_t *event);

/* Genuine stream discontinuity: bumps stream_epoch, invalidates every
 * timing assumption including learned polarity, discards any partial
 * field interpretation. Ordinary RF/DSP block boundaries must NEVER call
 * this. Returns the new epoch. */
uint32_t c5vrx_video_timing_invalidate(c5vrx_video_timing_state_t *state);

#ifdef __cplusplus
}
#endif
