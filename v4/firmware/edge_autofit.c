/* C5VRX by Twotoz/contributors: on-device EDGE AutoFit synthesis. */
#include "edge_autofit.h"
#define EDGE_AF_TABLE_DATA
#include "edge_autofit_table.h"
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

void edge_af_pinned(edge_af_params_t *p)
{
    p->phases = EDGE_AF_PHASES; p->frequencies = EDGE_AF_FREQUENCIES; p->output = EDGE_AF_OUTPUT;
    p->detector = EDGE_AF_DETECTOR; p->reliability = true;
    p->kp = EDGE_AF_KP; p->ki = EDGE_AF_KI; p->hold = EDGE_AF_HOLD; p->limit = EDGE_AF_LIMIT;
    p->mix = EDGE_AF_MIX; p->low_hz = EDGE_AF_LOW_HZ; p->high_hz = EDGE_AF_HIGH_HZ;
}

/* numpy remainder: result has the sign of the divisor. */
static double pymod(double a, double b)
{
    double m = fmod(a, b);
    if (m != 0.0 && ((m < 0.0) != (b < 0.0))) m += b;
    return m;
}

bool edge_af_synthesize(const edge_af_params_t *p, double fit_d, double fit_c, uint16_t low13[1024])
{
    const int P = p->phases, F = p->frequencies, T = 8;
    if (P < 4 || F < 2 || P * F * T != 1024 || p->output > EDGE_AF_OUT_AVG ||
        p->detector > EDGE_AF_DET_SOFTHOLD) return false;
    if (!(fit_d >= .4 && fit_d <= 3.0) || fabs(fit_c) > 4e6) return false;
    const double two_pi = 2 * M_PI;
    double lo = fit_c + fit_d * (p->low_hz - EDGE_AF_NOMINAL_HZ);
    double hi = fit_c + fit_d * (p->high_hz - EDGE_AF_NOMINAL_HZ);
    double fmin = two_pi * lo / 20e6, fmax = two_pi * hi / 20e6;
    if (!(fmin < fmax)) return false;
    double step = (fmax - fmin) / (F - 1);
    double cn = two_pi * EDGE_AF_NOMINAL_HZ / 20e6, cm = two_pi * fit_c / 20e6;
    for (int s = 0; s < P * F; ++s) {
        int fi = s / P, pi_ = s % P;
        double fr = fmin + (double)fi * step;
        double pr = (double)(pi_ * 2) * M_PI / P + fr;
        for (int t = 0; t < T; ++t) {
            double e = pymod(edge_af_obs[t] - pr + M_PI, two_pi) - M_PI;
            if (p->detector == EDGE_AF_DET_SOFTHOLD) {
                double u = e / p->hold;
                e = e * exp(-(u * u));
            } else {
                if (fabs(e) > p->hold) e = 0.0;
                if (p->detector == EDGE_AF_DET_TANH) e = p->limit * tanh(e / p->limit);
                else if (p->detector == EDGE_AF_DET_SINE) e = sin(e);
            }
            if (e < -p->limit) e = -p->limit;
            if (e > p->limit) e = p->limit;
            if (p->reliability) e = e * edge_af_rel[t];
            double target = fr + p->ki * e - 0.0;
            double fq = rint((target - fmin) / step);
            int f2 = fq < 0 ? 0 : fq > F - 1 ? F - 1 : (int)fq;
            int p2 = (int)floor((pr + p->kp * e) * P / two_pi + .5);
            p2 = ((p2 % P) + P) % P;
            double lv = fmin + (double)f2 * step, out;
            if (p->output == EDGE_AF_OUT_ADVANCE) out = fr + p->kp * e;
            else if (p->output == EDGE_AF_OUT_AVG) out = (fr + lv) / 2 + p->mix * p->kp * e;
            else out = lv + p->mix * p->kp * e;
            out = cn + (out - cm) / fit_d;
            double v = (out * 128 / M_PI - EDGE_AF_OFFSET) * EDGE_AF_SCALE;
            v = v < 0 ? 0 : v > 63 ? 63 : v;
            low13[s * T + t] = (uint16_t)((unsigned)rint(v) + ((unsigned)(f2 * P + p2) << 6));
        }
    }
    return true;
}

bool edge_af_fit(int sync_khz, int porch_khz, double *deviation, double *centre_hz)
{
    double d = (double)(porch_khz - sync_khz) / EDGE_AF_SYNC_TO_BLANK_KHZ;
    if (!(d >= .6 && d <= 2.2)) return false;
    *deviation = d;
    *centre_hz = ((double)porch_khz + d * EDGE_AF_BLANK_TO_CENTRE_KHZ) * 1000.0;
    return fabs(*centre_hz) <= 3.5e6;
}
