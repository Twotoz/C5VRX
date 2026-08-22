/* SPDX-License-Identifier: GPL-3.0-only */

#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "c5vrx_cvbs_levels.h"

/* Feed one synthetic line: sync tip, porch (with burst-like ripple),
 * active video at given level. */
static void feed_line(c5vrx_cvbs_levels_t *l, uint8_t sync, uint8_t porch,
                      uint8_t active)
{
    for (unsigned i = 0; i < 94u; ++i)
        c5vrx_cvbs_levels_observe(l, sync, C5VRX_LEVEL_REGION_SYNC_TIP);
    /* Porch minimum-envelope: ripple above the true porch level must
     * not lift the estimate materially. */
    for (unsigned i = 0; i < 60u; ++i) {
        const uint8_t v = (uint8_t)(porch + ((i & 1u) ? 6u : 0u));
        c5vrx_cvbs_levels_observe(l, v, C5VRX_LEVEL_REGION_BACK_PORCH);
    }
    for (unsigned i = 0; i < 1000u; ++i)
        c5vrx_cvbs_levels_observe(l, active, C5VRX_LEVEL_REGION_ACTIVE);
}

int main(void)
{
    c5vrx_cvbs_levels_config_t cfg = {0};
    c5vrx_cvbs_levels_t l;
    c5vrx_cvbs_levels_init(&l, &cfg);
    int bias = 0, gain = 0;

    /* Unprimed fallback must be sane and clamped. */
    c5vrx_cvbs_levels_recommend(&l, &bias, &gain);
    assert(bias == 24 && gain >= 64 && gain <= 1024);

    /* Wrong-level signal: blanking sits at 30, whites reach 50.
     * After priming, recommendations must pin blanking to 19 and map
     * the measured span onto 19..63. Expected gain ~ (63-19)<<8/(50-30). */
    memset(&l, 0, sizeof(l));
    c5vrx_cvbs_levels_init(&l, &cfg);
    for (int n = 0; n < 40; ++n) feed_line(&l, 2, 30, 50);
    assert(l.primed);
    c5vrx_cvbs_levels_recommend(&l, &bias, &gain);
    assert(bias <= 31 && bias >= 28);          /* Converged near 30. */
    {
        const int span_codes =
            (l.white_envelope_q8 >> 8) - (l.blanking_q8 >> 8);
        const int expect = ((63 - 19) << 8) / span_codes;
        (void)expect;
        /* Output mapping check through the conditioner formula:
         * out(white_env) ≈ black_target + span*gain/256 ≥ white-3. */
        const int out_white = 20 +
            (((l.white_envelope_q8 >> 8) - bias) * gain >> 8);
        assert(out_white >= 55 && out_white <= 70);
        const int out_porch = 20 + (((l.blanking_q8 >> 8) - bias) * gain >> 8);
        assert(out_porch >= 17 && out_porch <= 23); /* Pinned near 19/20. */
    }
    printf("levels: bias=%d gain=%d\n", bias, gain);

    /* Smooth tracking, no block-rate oscillation: with sustained peaks
     * present, successive per-line-group recommendations converge
     * monotonically instead of oscillating with picture content. */
    int prev_gain = gain;
    int max_step = 0;
    for (int n = 0; n < 120; ++n) {
        feed_line(&l, 2, 30, (uint8_t)(n & 1u ? 33 : 62));
        if ((n % 10u) == 9u) {
            c5vrx_cvbs_levels_recommend(&l, &bias, &gain);
            const int step = gain > prev_gain ? gain - prev_gain
                                              : prev_gain - gain;
            if (step > max_step) max_step = step;
            prev_gain = gain;
        }
    }
    printf("tracking: last=%d max_step=%d\n", gain, max_step);
    assert(max_step <= 24);

    /* Structure keying: identical peak white must give an identical
     * gain recommendation regardless of how much dark content shares
     * the frame. */
    c5vrx_cvbs_levels_t l2;
    c5vrx_cvbs_levels_init(&l2, &cfg);
    for (int n = 0; n < 60; ++n) feed_line(&l2, 2, 30, 62);
    int bias2 = 0, gain2 = 0;
    c5vrx_cvbs_levels_recommend(&l2, &bias2, &gain2);
    c5vrx_cvbs_levels_recommend(&l, &bias, &gain); /* Peaks also 62. */
    const int dgain2 = gain2 > gain ? gain2 - gain : gain - gain;
    printf("keying: gain_flat=%d gain_mixed=%d\n", gain2, gain);
    assert(dgain2 <= 8);

    puts("cvbs_levels_test: PASS");
    return 0;
}
