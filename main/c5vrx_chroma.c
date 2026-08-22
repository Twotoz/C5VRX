/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_chroma.h"

#include <math.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define BURST_START_NUM 112u /* ~5.6 us after sync leading edge. */
#define BURST_START_DEN 1280u
#define BURST_LEN_NUM 48u
#define BURST_LEN_DEN 1280u
#define ACTIVE_START_NUM 210u
#define ACTIVE_START_DEN 1280u
#define CHROMA_CENTER 31.5
#define BURST_LOCK_THRESHOLD 6.0
#define LOCK_ACQUIRE_LINES 4u
#define KILL_LINES 6u

static double wrap_pi(double a)
{
    while (a > M_PI) a -= 2.0 * M_PI;
    while (a <= -M_PI) a += 2.0 * M_PI;
    return a;
}

int c5vrx_chroma_init(c5vrx_chroma_t *c, const c5vrx_chroma_config_t *config)
{
    if (!c || !config || !config->sample_rate_hz ||
        !config->burst_freq_hz || !config->line_samples ||
        config->line_samples > C5VRX_CHROMA_MAX_LINE) {
        return -1;
    }
    memset(c, 0, sizeof(*c));
    c->cfg = *config;
    const double dtheta =
        2.0 * M_PI * (double)config->burst_freq_hz /
        (double)config->sample_rate_hz;
    c->step_c = cos(dtheta);
    c->step_s = sin(dtheta);
    c->osc_c = 1.0;
    c->osc_s = 0.0;
    c->pll_phase = 0.0;
    c->ma_period = config->sample_rate_hz / config->burst_freq_hz;
    if (c->ma_period < 3u) c->ma_period = 3u;
    if (c->ma_period > 63u) c->ma_period = 63u;
    if (!(c->ma_period & 1u)) ++c->ma_period; /* Odd => symmetric. */
    return 0;
}

void c5vrx_chroma_forget_prev_line(c5vrx_chroma_t *c)
{
    if (c) c->have_prev_line = false;
}

/* Report the oscillator's CURRENT sample phase, then advance one step.
 * Returning the pre-advance phase keeps the NCO aligned to absolute
 * sample index k (phase k*dtheta); advancing first would run it one
 * sample ahead of the stream and skew every measured burst angle by
 * -dtheta. */
static void osc_step(c5vrx_chroma_t *c, double *co, double *si)
{
    *co = c->osc_c;
    *si = c->osc_s;
    const double nc = c->osc_c * c->step_c - c->osc_s * c->step_s;
    const double ns = c->osc_c * c->step_s + c->osc_s * c->step_c;
    c->osc_c = nc;
    c->osc_s = ns;
}

bool c5vrx_chroma_process_line(c5vrx_chroma_t *c,
                               const uint8_t *composite,
                               const uint8_t *prev_active,
                               size_t count,
                               uint8_t *y_out,
                               int8_t *u_out,
                               int8_t *v_out)
{
    if (!c || !composite || !y_out || !u_out || !v_out ||
        count > C5VRX_CHROMA_MAX_LINE) {
        return false;
    }
    const size_t bstart = count * 86u / 1000u + 24u;
    size_t blen = count * BURST_LEN_NUM / BURST_LEN_DEN;
    if (bstart + blen > count) blen = count > bstart ? count - bstart : 0u;

    /* ---- Burst window: correlate against the continuous NCO. */
    double bi = 0.0, bq = 0.0;
    for (size_t i = 0; i < bstart; ++i) {
        double co, si;
        osc_step(c, &co, &si);
    }
    for (size_t i = 0; i < blen; ++i) {
        const double v = ((int)composite[bstart + i] - CHROMA_CENTER) /
                         32.0;
        double co, si;
        osc_step(c, &co, &si);
        bi += v * co;
        bq += v * si;
    }
    const double amp = 2.0 * sqrt(bi * bi + bq * bq) /
                       (blen ? (double)blen : 1.0) * 32.0;
    const bool burst_present = blen >= 16u && amp >= BURST_LOCK_THRESHOLD;
    c->bursts_seen += burst_present ? 1u : 0u;
    c->bursts_missing += burst_present ? 0u : 1u;
    c->burst_amplitude += (amp - c->burst_amplitude) / 4.0;

    if (burst_present) {
        const double measured = atan2(bq, bi);
        if (c->cfg.pal && !c->have_switch_evidence) {
            /* First ever burst anchors the V-axis state to evidence. */
            c->pal_switch = measured >= 0.0;
            c->have_switch_evidence = true;
        }
        double expected = c->cfg.pal ?
            (c->pal_switch ? 3.0 * M_PI / 4.0 : -3.0 * M_PI / 4.0) :
            M_PI;
        double err = wrap_pi(measured - expected);
        if (c->cfg.pal && fabs(err) >= M_PI / 2.0 - 1e-9) {
            /* Opposite quadrant: the V-axis switched this line. */
            c->pal_switch = !c->pal_switch;
            ++c->switch_flips;
            expected = -expected;
            err = wrap_pi(measured - expected);
        }
        c->pll_phase = wrap_pi(c->pll_phase + err * 0.25);
        if (c->lock_score < LOCK_ACQUIRE_LINES) ++c->lock_score;
        else if (c->unlock_score) --c->unlock_score;
    } else {
        if (c->unlock_score < KILL_LINES) ++c->unlock_score;
        else if (c->lock_score) --c->lock_score;
    }

    const bool was_locked = c->color_lock;
    if (!c->color_lock && c->lock_score >= LOCK_ACQUIRE_LINES &&
        c->unlock_score == 0u) {
        c->color_lock = true;
        c->have_prev_line = false; /* Comb needs fresh continuity. */
    } else if (c->color_lock && c->unlock_score >= KILL_LINES) {
        c->color_lock = false;
        c->lock_score = 0u;
        c->unlock_score = 0u;
        c->have_switch_evidence = false;
    }
    (void)was_locked;

    /* ---- Active region: Y/C separation + quadrature demodulation.
     * Reference rotation is fixed within the line: compute it once.
     * Only PAL carries the V-axis sign flip; NTSC keeps V positive.
     *
     * The demodulator input must be pedestal-free WITHOUT phase shift:
     * a notch/differentiator rotates the chroma phasor and leaks U into
     * V. Instead, subtract a centered moving average over one carrier
     * period (symmetric => zero phase; passes pedestal, nulls carrier).
     * Mixing then maps chroma onto baseband (per-sample mean of
     * ac*sin == U/32), so a one-pole lowpass plus 2:1 decimation yields
     * stable half-rate U/V without carrier ripple. */
    const size_t astart = count * ACTIVE_START_NUM / ACTIVE_START_DEN;
    const int v_sign = (c->cfg.pal && !c->pal_switch) ? -1 : 1;
    const double ref_c = cos(-c->pll_phase);
    const double ref_s = sin(-c->pll_phase);
    const unsigned ma_half = c->ma_period / 2u;

    /* Pass A: centered moving-average reference into scratch. */
    for (size_t n = astart; n < count; ++n) {
        size_t lo = n > ma_half ? n - ma_half : n;
        if (lo < astart) lo = astart;
        size_t hi = n + ma_half + 1u < count ? n + ma_half + 1u : count;
        unsigned sum = 0u;
        for (size_t k = lo; k < hi; ++k) sum += composite[k];
        c->prev_line[n] = (uint8_t)(sum / (unsigned)(hi - lo));
    }

    /* Pass B: demodulate. */
    double u_lp = 0.0, v_lp = 0.0;
    unsigned bin_phase = 0u;
    unsigned out_idx = 0u;
    const unsigned half_bins = (unsigned)(count / 2u);

    for (size_t n = astart; n < count; ++n) {
        double co, si;
        osc_step(c, &co, &si);
        const int x = composite[n];
        const int xp = prev_active ? prev_active[n] : x;

        /* Y/C separation: prefer the 1H comb (phase-neutral), fall back
         * to the moving-average luma reference. Without color lock pass
         * luma untouched. */
        int y;
        if (!c->color_lock) {
            y = x;
        } else if (prev_active) {
            y = (x + xp + 1) >> 1;
        } else {
            y = c->prev_line[n];
        }
        y_out[n] = (uint8_t)(y < 0 ? 0 : (y > 63 ? 63 : y));

        /* Reference MUST come from the returned pre-advance phase: the
         * struct state already points one sample ahead. */
        const double rc = co * ref_c - si * ref_s;
        const double rs = co * ref_s + si * ref_c;
        const double ac = ((int)x - (int)c->prev_line[n]) / 32.0;
        u_lp += (ac * rs - u_lp) * 0.25;
        v_lp += (-ac * rc * v_sign - v_lp) * 0.25;

        if (++bin_phase >= 2u) {
            bin_phase = 0u;
            if (out_idx < half_bins) {
                /* Scale: per-sample mean of ac*ref == U/32, so 48 maps
                 * code-unit chroma into a comfortable int8 range. */
                const double scale = 48.0;
                double u = u_lp * scale;
                double v = v_lp * scale;
                if (!c->color_lock) { u = 0.0; v = 0.0; }
                u_out[out_idx] = (int8_t)(u > 126 ? 126 :
                                          u < -127 ? -127 : u);
                v_out[out_idx] = (int8_t)(v > 126 ? 126 :
                                          v < -127 ? -127 : v);
                ++out_idx;
            }
        }
    }

    /* Remember this line's active samples for the next 1H comb. */
    if (count <= C5VRX_CHROMA_MAX_LINE) {
        memcpy(c->prev_line, composite, count);
        c->have_prev_line = true;
    }
    return c->color_lock;
}
