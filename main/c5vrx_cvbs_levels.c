/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_cvbs_levels.h"

#include <string.h>

#define DEFAULT_SHIFT 5u   /* ~1/32 per update: slow, structure keyed. */
#define MIN_SPAN_Q8 (4 << 8)
#define GAIN_MIN_Q8 64     /* 0.25x floor. */
#define GAIN_MAX_Q8 1024   /* 4x ceiling. */
#define GAIN_SLEW_PER_CALL 6
#define BIAS_SLEW_PER_CALL 1

void c5vrx_cvbs_levels_init(c5vrx_cvbs_levels_t *levels,
                            const c5vrx_cvbs_levels_config_t *config)
{
    if (!levels) return;
    memset(levels, 0, sizeof(*levels));
    levels->cfg.blank_target = config && config->blank_target ?
        config->blank_target : 19u;
    levels->cfg.white_target = config && config->white_target ?
        config->white_target : 63u;
    levels->cfg.shift = config ? config->shift : 0u;
    levels->sync_tip_q8 = 2 << 8;
    levels->blanking_q8 = 24 << 8;
    levels->white_envelope_q8 = 52 << 8;
    levels->out_bias_q8 = 24;
    {
        /* Idle default gain for the unprimed fallback mapping. */
        const int span = (52 - 24) << 8;
        levels->out_gain_q8 =
            (int)((((int64_t)44) << 16) / (span < MIN_SPAN_Q8 ?
                                           MIN_SPAN_Q8 : span));
        if (levels->out_gain_q8 > GAIN_MAX_Q8)
            levels->out_gain_q8 = GAIN_MAX_Q8;
    }
}

void c5vrx_cvbs_levels_observe(c5vrx_cvbs_levels_t *levels,
                               uint8_t sample,
                               c5vrx_level_region_t region)
{
    if (!levels) return;
    sample &= 0x3fu;
    const unsigned shift = levels->cfg.shift ? levels->cfg.shift
                                             : DEFAULT_SHIFT;

    switch (region) {
        case C5VRX_LEVEL_REGION_SYNC_TIP:
            /* Minimum envelope with slightly faster attack than the
             * porch tracker so deep tips are learned quickly. */
            levels->sync_tip_q8 +=
                (((int32_t)sample << 8) - levels->sync_tip_q8) >>
                (shift > 1u ? shift - 1u : 1u);
            ++levels->sync_updates;
            break;
        case C5VRX_LEVEL_REGION_BACK_PORCH:
            /* Minimum-envelope semantics via asymmetric step: samples
             * below the estimate pull it down fully; samples above pull
             * it up only by the smoothing share, so burst riding on the
             * porch cannot lift the blanking estimate materially. */
            if (((int32_t)sample << 8) < levels->blanking_q8) {
                levels->blanking_q8 = (int32_t)sample << 8;
            } else {
                levels->blanking_q8 -=
                    (levels->blanking_q8 - ((int32_t)sample << 8)) >> shift;
            }
            ++levels->porch_updates;
            break;
        case C5VRX_LEVEL_REGION_ACTIVE: {
            /* Fast-attack upper envelope toward true peak white, with
             * a multi-second release: gain must follow transmission
             * structure, never picture brightness (no AGC pumping). */
            const int32_t value_q8 = (int32_t)sample << 8;
            if (value_q8 > levels->white_envelope_q8) {
                levels->white_envelope_q8 +=
                    (value_q8 - levels->white_envelope_q8) >> 2;
            }
            if (++levels->active_samples >= 4096u) {
                levels->active_samples = 0u;
                const int32_t floor_q8 =
                    levels->blanking_q8 + (4 << 8);
                levels->white_envelope_q8 -=
                    (levels->white_envelope_q8 - floor_q8) >>
                    (shift + 9u);
            }
            if (sample >= 62u) ++levels->clipping_events;
            break;
        }
        case C5VRX_LEVEL_REGION_OTHER:
        default:
            break;
    }
    levels->primed = levels->porch_updates > 100u &&
                     levels->sync_updates > 10u;
}

void c5vrx_cvbs_levels_recommend(c5vrx_cvbs_levels_t *levels,
                                 int *bias,
                                 int *gain)
{
    if (!bias || !gain) return;

    int32_t blanking_q8 = 24 << 8;
    int32_t white_q8 = 52 << 8;
    const bool primed = levels && levels->primed;
    if (primed) {
        blanking_q8 = levels->blanking_q8;
        white_q8 = levels->white_envelope_q8;
    }

    int32_t span = white_q8 - blanking_q8;
    if (span < MIN_SPAN_Q8) span = MIN_SPAN_Q8;

    const int32_t target_span =
        levels ? (int32_t)(levels->cfg.white_target -
                           levels->cfg.blank_target) : 44;
    /* span is Q8 (codes << 8): target codes shifted by 16 produce gain
     * directly in Q8 (256 = 1.0). */
    int32_t gain_target =
        (int32_t)(((int64_t)(target_span > 0 ? target_span : 44)
                   << 16) / span);
    if (gain_target < GAIN_MIN_Q8) gain_target = GAIN_MIN_Q8;
    if (gain_target > GAIN_MAX_Q8) gain_target = GAIN_MAX_Q8;
    const int bias_target = blanking_q8 >> 8;

    int out_gain, out_bias;
    if (!primed) {
        out_gain = levels ? levels->out_gain_q8 : gain_target;
        out_bias = levels ? levels->out_bias_q8 : bias_target;
    } else {
        const bool latch_first = levels && !levels->recommend_latched;
        levels->recommend_latched = true;
        int32_t g = levels->out_gain_q8;
        int b = levels->out_bias_q8;
        if (latch_first) {
            g = gain_target;
            b = bias_target;
        } else {
            const int32_t dgain = gain_target - g;
            if (dgain > GAIN_SLEW_PER_CALL) g += GAIN_SLEW_PER_CALL;
            else if (dgain < -GAIN_SLEW_PER_CALL) g -= GAIN_SLEW_PER_CALL;
            else g = gain_target;
            const int dbias = bias_target - b;
            if (dbias > BIAS_SLEW_PER_CALL) b += BIAS_SLEW_PER_CALL;
            else if (dbias < -BIAS_SLEW_PER_CALL) b -= BIAS_SLEW_PER_CALL;
            else b = bias_target;
        }
        levels->out_gain_q8 = g;
        levels->out_bias_q8 = b;
        out_gain = g;
        out_bias = b;
    }

    *bias = out_bias;
    *gain = out_gain;
}
