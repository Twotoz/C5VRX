/* SPDX-License-Identifier: GPL-3.0-only */
#include "c5vrx_iq_video.h"
#include "c5vrx_dac.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#define PI_F 3.14159265358979323846f
#define MAX_SYNC_EDGES 64u

static int16_t sign10(uint32_t value)
{
    value &= 0x3ffu;
    return (value & 0x200u) ? (int16_t)(value - 0x400u) : (int16_t)value;
}

void c5vrx_iq_video_dc_init(c5vrx_iq_video_dc_t *dc)
{
    if (dc) memset(dc, 0, sizeof(*dc));
}

void c5vrx_iq_video_dc_add(c5vrx_iq_video_dc_t *dc,
                           const uint32_t *packed_iq, size_t count)
{
    if (!dc || !packed_iq) return;
    int32_t previous_i = 0, previous_q = 0;
    bool primed = false;
    for (size_t n = 0; n < count; ++n) {
        const int32_t q = sign10(packed_iq[n]);
        const int32_t i = sign10(packed_iq[n] >> 10);
        dc->sum_i += i;
        dc->sum_q += q;
        dc->sum_power += (uint64_t)(i * i + q * q);
        if (i <= -511 || i >= 510 || q <= -511 || q >= 510) dc->clipping++;
        if (primed) {
            const int64_t dot = (int64_t)i * previous_i +
                                (int64_t)q * previous_q;
            const int64_t cross = (int64_t)q * previous_i -
                                  (int64_t)i * previous_q;
            if (dot > 0) dc->dot_sum += (uint64_t)dot;
            dc->abs_cross_sum += (uint64_t)(cross < 0 ? -cross : cross);
        }
        previous_i = i;
        previous_q = q;
        primed = true;
    }
    dc->samples += (uint32_t)count;
}

void c5vrx_iq_video_dc_finish(const c5vrx_iq_video_dc_t *dc,
                              c5vrx_iq_video_result_t *result)
{
    if (!dc || !result || !dc->samples) return;
    result->i_dc = (int32_t)(dc->sum_i / dc->samples);
    result->q_dc = (int32_t)(dc->sum_q / dc->samples);
    result->clipping = dc->clipping;
    result->valid_iq = dc->sum_power / dc->samples > 16u;
    const uint64_t denominator = dc->dot_sum + dc->abs_cross_sum;
    result->phase_coherence_ppm = denominator ?
        (uint32_t)(dc->dot_sum * 1000000ull / denominator) : 0u;
}

size_t c5vrx_iq_video_demodulate_4to1(const uint32_t *packed_iq,
                                      size_t count, int32_t dc_i,
                                      int32_t dc_q, int16_t *out,
                                      size_t capacity)
{
    if (!packed_iq || !out || count < 8u || capacity < count / 4u)
        return 0u;
    const size_t output_count = count / 4u;
    out[0] = 0; /* boundary is missing time, never an adjacent discriminator */
    for (size_t k = 1; k < output_count; ++k) {
        const size_t n = k * 4u;
        const int32_t q = sign10(packed_iq[n]) - dc_q;
        const int32_t i = sign10(packed_iq[n] >> 10) - dc_i;
        const int32_t pq = sign10(packed_iq[n - 4u]) - dc_q;
        const int32_t pi = sign10(packed_iq[n - 4u] >> 10) - dc_i;
        const int64_t real = (int64_t)i * pi + (int64_t)q * pq;
        const int64_t imag = (int64_t)q * pi - (int64_t)i * pq;
        int value = (int)lrintf(atan2f((float)imag, (float)real) *
                                      (256.0f / (2.0f * PI_F)));
        if (value < -128) value = -128;
        if (value > 127) value = 127;
        out[k] = (int16_t)value;
    }
    return output_count;
}

static int compare_i16(const void *a, const void *b)
{
    const int av = *(const int16_t *)a;
    const int bv = *(const int16_t *)b;
    return (av > bv) - (av < bv);
}

static int median_i16(int16_t *values, size_t count)
{
    if (!count) return 0;
    qsort(values, count, sizeof(*values), compare_i16);
    return values[count / 2u];
}

typedef struct {
    uint32_t starts[MAX_SYNC_EDGES];
    unsigned edges;
    unsigned cadence_hits;
    uint32_t period;
    uint32_t score;
    int polarity;
} sync_candidate_t;

static sync_candidate_t find_sync(const int16_t *raw, size_t count,
                                  int polarity)
{
    sync_candidate_t candidate = {.polarity = polarity};
    int16_t *sorted = malloc(count * sizeof(*sorted));
    if (!sorted) return candidate;
    for (size_t i = 0; i < count; ++i) sorted[i] = raw[i] * polarity;
    qsort(sorted, count, sizeof(*sorted), compare_i16);
    const int p08 = sorted[count * 8u / 100u];
    const int median = sorted[count / 2u];
    const int threshold = p08 + (median - p08) / 3;
    free(sorted);

    size_t i = 1u;
    while (i < count && candidate.edges < MAX_SYNC_EDGES) {
        if (raw[i] * polarity >= threshold) { ++i; continue; }
        const size_t start = i;
        while (i < count && raw[i] * polarity < threshold) ++i;
        const size_t width = i - start;
        if (width >= 50u && width <= 150u)
            candidate.starts[candidate.edges++] = (uint32_t)start;
    }
    uint64_t period_sum = 0u;
    for (unsigned edge = 1u; edge < candidate.edges; ++edge) {
        const uint32_t period = candidate.starts[edge] -
                                candidate.starts[edge - 1u];
        if (period >= 1180u && period <= 1360u) {
            period_sum += period;
            candidate.cadence_hits++;
        }
    }
    if (candidate.cadence_hits)
        candidate.period = (uint32_t)(period_sum / candidate.cadence_hits);
    candidate.score = candidate.cadence_hits * 1000u + candidate.edges * 10u;
    if (candidate.period >= 1240u && candidate.period <= 1310u)
        candidate.score += 500u;
    return candidate;
}

void c5vrx_iq_video_analyze_cvbs(const int16_t *raw, size_t count,
                                 c5vrx_iq_video_result_t *result)
{
    if (!raw || !result || count < 3000u) return;
    const sync_candidate_t positive = find_sync(raw, count, 1);
    const sync_candidate_t negative = find_sync(raw, count, -1);
    const sync_candidate_t *sync = positive.score >= negative.score ?
        &positive : &negative;
    result->polarity = sync->polarity;
    const uint32_t other_score = sync == &positive ? negative.score : positive.score;
    result->polarity_confidence = sync->score ?
        (sync->score > other_score ?
            (sync->score - other_score) * 1000u / sync->score : 0u) : 0u;
    result->samples_per_line_q16 = sync->period << 16u;

    int16_t sync_values[4096];
    int16_t blank_values[2048];
    size_t sync_n = 0u, blank_n = 0u;
    for (unsigned edge = 0; edge < sync->edges; ++edge) {
        const size_t start = sync->starts[edge];
        for (size_t n = start + 8u; n < start + 70u && n < count; ++n)
            if (sync_n < 4096u) sync_values[sync_n++] = raw[n] * sync->polarity;
        /* Quiet back porch after burst; never use the burst as blank level. */
        for (size_t n = start + 170u; n < start + 200u && n < count; ++n)
            if (blank_n < 2048u) blank_values[blank_n++] = raw[n] * sync->polarity;
    }
    result->sync_level = median_i16(sync_values, sync_n);
    result->blank_level = median_i16(blank_values, blank_n);

    /* Burst discrimination is deliberately subordinate to H-sync. */
    double pal_energy = 0.0, ntsc_energy = 0.0;
    for (unsigned edge = 0; edge < sync->edges; ++edge) {
        const size_t start = sync->starts[edge] + 108u;
        if (start + 52u >= count) continue;
        double pal_re = 0, pal_im = 0, ntsc_re = 0, ntsc_im = 0;
        for (unsigned j = 0; j < 52u; ++j) {
            const double value = raw[start + j] * sync->polarity -
                                 result->blank_level;
            const double pp = 2.0 * 3.14159265358979323846 *
                4433618.75 * j / C5VRX_RAW_CVBS_RATE_HZ;
            const double np = 2.0 * 3.14159265358979323846 *
                3579545.0 * j / C5VRX_RAW_CVBS_RATE_HZ;
            pal_re += value * cos(pp); pal_im += value * sin(pp);
            ntsc_re += value * cos(np); ntsc_im += value * sin(np);
        }
        pal_energy += pal_re * pal_re + pal_im * pal_im;
        ntsc_energy += ntsc_re * ntsc_re + ntsc_im * ntsc_im;
    }
    result->pal_score = (uint32_t)(pal_energy > 4294967295.0 ?
                                   4294967295.0 : pal_energy);
    result->ntsc_score = (uint32_t)(ntsc_energy > 4294967295.0 ?
                                    4294967295.0 : ntsc_energy);
    result->burst_detected = pal_energy + ntsc_energy > 1000.0;
    const bool pal = pal_energy >= ntsc_energy;
    /* Estimate, rather than merely print, the burst frequency. The search is
     * bounded to the color-burst band and runs only during ACQUIRE. A wrong
     * represented sample time therefore moves the reported peak and cannot
     * silently masquerade as the nominal PAL/NTSC subcarrier. */
    double best_burst_energy = 0.0;
    uint32_t best_burst_hz = 0u;
    for (uint32_t hz = 3200000u; hz <= 4800000u; hz += 10000u) {
        double energy = 0.0;
        for (unsigned edge = 0; edge < sync->edges; ++edge) {
            const size_t start = sync->starts[edge] + 108u;
            if (start + 52u >= count) continue;
            double re = 0.0, im = 0.0;
            for (unsigned j = 0; j < 52u; ++j) {
                const double value = raw[start + j] * sync->polarity -
                                     result->blank_level;
                const double phase = 2.0 * 3.14159265358979323846 *
                                     hz * j / C5VRX_RAW_CVBS_RATE_HZ;
                re += value * cos(phase);
                im += value * sin(phase);
            }
            energy += re * re + im * im;
        }
        if (energy > best_burst_energy) {
            best_burst_energy = energy;
            best_burst_hz = hz;
        }
    }
    result->burst_frequency_hz = best_burst_hz;
    result->burst_amplitude_q16 = (uint32_t)(sqrt(pal ? pal_energy : ntsc_energy) * 65536.0 /
                                             (sync->edges ? sync->edges * 52u : 1u));
    const uint32_t line_hz = pal ? 15625u : 15734u;
    result->native_sample_time_hz = sync->period * 4u * line_hz;
    const uint32_t difference = result->native_sample_time_hz > C5VRX_RF_IQ_TIMEBASE_HZ ?
        result->native_sample_time_hz - C5VRX_RF_IQ_TIMEBASE_HZ :
        C5VRX_RF_IQ_TIMEBASE_HZ - result->native_sample_time_hz;
    result->sample_time_confidence = difference >= C5VRX_RF_IQ_TIMEBASE_HZ ? 0u :
        (C5VRX_RF_IQ_TIMEBASE_HZ - difference) * 1000u / C5VRX_RF_IQ_TIMEBASE_HZ;

    const int span = result->blank_level - result->sync_level;
    if (span > 1) {
        result->recommended_gain_q8 = (18 * 256 + span / 2) / span;
        result->recommended_bias_q8 = -result->sync_level *
                                      result->recommended_gain_q8;
        for (int input = -128; input <= 127; ++input) {
            int code = (input * result->recommended_gain_q8 +
                        result->recommended_bias_q8 + 128) >> 8;
            if (code < 0) code = 0;
            if (code > C5VRX_DAC_WHITE_CODE) code = C5VRX_DAC_WHITE_CODE;
            result->map[(uint8_t)input] = (uint8_t)code;
        }
    }
    const bool cadence_valid = sync->cadence_hits >= 3u &&
        sync->period >= 1180u && sync->period <= 1360u && span > 1;
    if (!result->valid_iq) result->classification = C5VRX_IQ_VIDEO_NO_RF;
    else if (!cadence_valid) result->classification =
        C5VRX_IQ_VIDEO_RF_NO_VALID_VIDEO;
    else if (!result->burst_detected) result->classification =
        C5VRX_IQ_VIDEO_UNCERTAIN;
    else result->classification = pal ? C5VRX_IQ_VIDEO_PAL_VALID :
                                        C5VRX_IQ_VIDEO_NTSC_VALID;
}

const char *c5vrx_iq_video_classification_name(
    c5vrx_iq_video_classification_t classification)
{
    switch (classification) {
        case C5VRX_IQ_VIDEO_NO_RF: return "NO_RF";
        case C5VRX_IQ_VIDEO_RF_NO_VALID_VIDEO: return "RF_NO_VALID_VIDEO";
        case C5VRX_IQ_VIDEO_PAL_VALID: return "PAL_VALID";
        case C5VRX_IQ_VIDEO_NTSC_VALID: return "NTSC_VALID";
        case C5VRX_IQ_VIDEO_UNCERTAIN: return "UNCERTAIN";
        default: return "UNKNOWN";
    }
}
