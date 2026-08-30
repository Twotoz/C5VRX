/* SPDX-License-Identifier: GPL-3.0-only */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define C5VRX_RF_IQ_TIMEBASE_HZ 80000000u
#define C5VRX_RAW_CVBS_RATE_HZ  20000000u
#define C5VRX_RF_BLOCK_WORDS    16384u
#define C5VRX_CVBS_BLOCK_SAMPLES 4096u

typedef enum {
    C5VRX_IQ_VIDEO_NO_RF = 0,
    C5VRX_IQ_VIDEO_RF_NO_VALID_VIDEO,
    C5VRX_IQ_VIDEO_PAL_VALID,
    C5VRX_IQ_VIDEO_NTSC_VALID,
    C5VRX_IQ_VIDEO_UNCERTAIN,
} c5vrx_iq_video_classification_t;

typedef struct {
    c5vrx_iq_video_classification_t classification;
    bool valid_iq;
    bool burst_detected;
    int32_t i_dc;
    int32_t q_dc;
    uint32_t clipping;
    uint32_t phase_coherence_ppm;
    uint32_t pal_score;
    uint32_t ntsc_score;
    int polarity;
    uint32_t polarity_confidence;
    uint32_t samples_per_line_q16;
    uint32_t native_sample_time_hz;
    uint32_t sample_time_confidence;
    int32_t sync_level;
    int32_t blank_level;
    uint32_t burst_frequency_hz;
    uint32_t burst_amplitude_q16;
    int32_t recommended_gain_q8;
    int32_t recommended_bias_q8;
    uint8_t map[256];
} c5vrx_iq_video_result_t;

typedef struct {
    int64_t sum_i;
    int64_t sum_q;
    uint64_t sum_power;
    uint64_t dot_sum;
    uint64_t abs_cross_sum;
    uint32_t samples;
    uint32_t clipping;
} c5vrx_iq_video_dc_t;

void c5vrx_iq_video_dc_init(c5vrx_iq_video_dc_t *dc);
void c5vrx_iq_video_dc_add(c5vrx_iq_video_dc_t *dc,
                           const uint32_t *packed_iq, size_t count);
void c5vrx_iq_video_dc_finish(const c5vrx_iq_video_dc_t *dc,
                              c5vrx_iq_video_result_t *result);

/* dphi4[k] = angle(x[4k] * conj(x[4k-4])). out[0] is neutral priming. */
size_t c5vrx_iq_video_demodulate_4to1(const uint32_t *packed_iq,
                                      size_t count, int32_t dc_i,
                                      int32_t dc_q, int16_t *out,
                                      size_t capacity);

void c5vrx_iq_video_analyze_cvbs(const int16_t *raw, size_t count,
                                 c5vrx_iq_video_result_t *result);
const char *c5vrx_iq_video_classification_name(
    c5vrx_iq_video_classification_t classification);

#ifdef __cplusplus
}
#endif
