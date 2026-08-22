/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* PAL/NTSC color decoder core (issue #5 section 8).
 *
 * Burst-window detection, burst frequency/phase lock via an NCO whose
 * phase/frequency are trimmed from real burst evidence, quadrature
 * chroma demodulation into U/V, PAL V-axis switch handling derived from
 * observed burst phase alternation (never assumed line counters), a 1H
 * line-comb Y/C separator with a notch fallback, and a clean color
 * killer: without reliable burst lock the output is grayscale - never
 * fake or random hue.
 *
 * Field identity, parity and line placement are NOT decided here; they
 * come from the canonical timing observer upstream (one timing
 * authority). This module only owns the chroma decoder loop. */

#define C5VRX_CHROMA_MAX_LINE 2048u

typedef struct {
    uint32_t sample_rate_hz;
    uint32_t burst_freq_hz;      /* PAL 4433619, NTSC 3579545. */
    uint32_t line_samples;       /* Measured line period in samples. */
    int pal;                     /* PAL V-axis alternation expected. */
} c5vrx_chroma_config_t;

typedef struct {
    c5vrx_chroma_config_t cfg;
    /* Continuous oscillator recurrence (advanced once per sample). */
    double osc_c, osc_s;
    double step_c, step_s;
    /* Carrier period in samples (rounded); drives the centered
     * moving-average chroma reference. */
    unsigned ma_period;
    /* Burst PLL. */
    double pll_phase;            /* Locked carrier phase estimate. */
    double burst_amplitude;      /* Smoothed burst amplitude (code units). */
    bool color_lock;
    bool pal_switch;             /* V polarity of the current line. */
    bool have_switch_evidence;
    unsigned lock_score;
    unsigned unlock_score;
    /* 1H comb memory. */
    uint8_t prev_line[C5VRX_CHROMA_MAX_LINE];
    bool have_prev_line;
    /* Telemetry. */
    uint64_t bursts_seen;
    uint64_t bursts_missing;
    uint64_t switch_flips;
} c5vrx_chroma_t;

int c5vrx_chroma_init(c5vrx_chroma_t *chroma,
                      const c5vrx_chroma_config_t *config);
/* Process one complete line of conditioned composite samples.
 * prev_active may be NULL (notch fallback instead of 1H comb).
 * y_out receives count samples; u_out/v_out receive count/2 half-rate
 * samples. Returns the color-lock state used for this line. */
bool c5vrx_chroma_process_line(c5vrx_chroma_t *chroma,
                               const uint8_t *composite,
                               const uint8_t *prev_active,
                               size_t count,
                               uint8_t *y_out,
                               int8_t *u_out,
                               int8_t *v_out);

void c5vrx_chroma_forget_prev_line(c5vrx_chroma_t *chroma);

#ifdef __cplusplus
}
#endif
