/* C5VRX by Twotoz/contributors: qualified circle evidence and DC-aware tokens.
 * Extends dsp_circle_dc and the archived U85Q/kp_morph observation law.
 * Carrier-fit qualification is used by calibration collect(). Token-centering
 * remains research: LUT writes require the existing verified halted restart,
 * never writes to a running core. */
#pragma once
#include "dsp.h"

/* A few quantizer cells can fit a perfect tiny circle with env=9999 while
 * the VTX is off. Envelope evidence alone is not an identifiable DC centre.
 * Require variance in both independent directions and a reasonably balanced
 * cloud before accepting the existing production circle fit. */
static inline bool dc_live_fit(const dsp_circle_t *c, unsigned rail_pm,
                               int *i_mcells, int *q_mcells, unsigned *env_x100)
{
    if (!c || c->n < 1024 || rail_pm >= 20) return false;
    const double n = (double)c->n, mx = c->x / n, my = c->y / n;
    const double xx = c->xx / n - mx * mx, yy = c->yy / n - my * my;
    const double xy = c->xy / n - mx * my;
    const double spread = sqrt((xx - yy) * (xx - yy) + 4 * xy * xy);
    const double low = .5 * (xx + yy - spread), high = .5 * (xx + yy + spread);
    /* dsp coordinates are twice a cell; low >=4 means >=1 cell^2. */
    if (!(low >= 4. && low >= .2 * high)) return false;
    int i, q; unsigned env;
    if (!dsp_circle_dc(c, &i, &q, &env) || env < 300) return false;
    if (i_mcells) *i_mcells = i;
    if (q_mcells) *q_mcells = q;
    if (env_x100) *env_x100 = env;
    return true;
}

/* Preserve the established noise mean and envelope diagnostic. A rejected
 * tiny/partial circle must not publish its fitted centre as receiver DC. */
static inline bool dc_live_measure(const dsp_circle_t *c, unsigned rail_pm,
    int *i_mcells, int *q_mcells, unsigned *env_x100)
{
    bool circle = dsp_circle_dc(c, i_mcells, q_mcells, env_x100);
    if (!circle) return false;
    if (dc_live_fit(c, rail_pm, i_mcells, q_mcells, env_x100)) return true;
    *i_mcells = (int)((double)c->x * 500. / (double)c->n);
    *q_mcells = (int)((double)c->y * 500. / (double)c->n);
    return false;
}

/* Controller address is I[3:0], Q[3:0], then two middle-sample signs. U85Q
 * currently uses the endpoint for its angle. Change top-five token bits only;
 * the shared worker's phase state and DAC bits must remain bit-exact. */
static inline void dc_live_center_lut(uint16_t *dst, const uint16_t *base,
                                      int i_mcells, int q_mcells)
{
    if (!i_mcells && !q_mcells) {
        for (unsigned a = 0; a < 1024; ++a) dst[a] = base[a];
        return;
    }
    const double dc_i = i_mcells / 1000., dc_q = q_mcells / 1000.;
    for (unsigned a = 0; a < 1024; ++a) {
        int i = (int)(a & 15u), q = (int)((a >> 4) & 15u);
        if (i > 7) i -= 16;
        if (q > 7) q -= 16;
        const double x = i + .5 - dc_i, y = q + .5 - dc_q;
        const double angle = atan2(y, x) + M_PI;
        unsigned token = (unsigned)floor(angle * 28. / (2. * M_PI)) % 28u;
        if (x * x + y * y < .64)
            token = 28u + (unsigned)floor(angle / (M_PI / 2.)) % 4u;
        dst[a] = (uint16_t)((base[a] & 2047u) | (token << 11));
    }
}
