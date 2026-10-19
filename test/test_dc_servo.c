/* C5VRX-5 by Twotoz and the C5VRX contributors: host test of the live DC
 * servo (main/dc_servo.h) against a simulated receiver: a DAC with an unknown
 * slope and polarity per axis, a boot offset of up to 4 cells, a 4-bit
 * quantiser that clips, receiver noise, and a wandering DC.
 *
 *   cc -O2 -std=c11 -I../main test_dc_servo.c -lm -o t && ./t
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include "dc_servo.h"

static unsigned s_fail, s_run;
static void check(const char *name, int ok)
{
    ++s_run;
    printf("%-4s %s\n", ok ? "ok" : "FAIL", name);
    s_fail += !ok;
}

static uint32_t s_seed = 2026;
static double gauss(void)
{
    double u = 0;
    for (int k = 0; k < 12; ++k) { s_seed = s_seed * 1664525u + 1013904223u; u += (s_seed >> 8) / 16777216.0; }
    return u - 6.0;
}

/* Mean of n quantised samples (dsp units summed like gain.c) of noise sigma
 * around a true DC (cells), clipped to the 4-bit range. */
static void measure(double dc_i, double dc_q, double sigma, uint32_t n, int64_t *si, int64_t *sq)
{
    *si = *sq = 0;
    for (uint32_t k = 0; k < n; ++k) {
        int i = (int)floor(dc_i + sigma * gauss()), q = (int)floor(dc_q + sigma * gauss());
        i = i < -8 ? -8 : i > 7 ? 7 : i;
        q = q < -8 ? -8 : q > 7 ? 7 : q;
        *si += 2 * i + 1;
        *sq += 2 * q + 1;
    }
}

/* Run the loop: returns the |DC| (mcells) after `periods`, worst axis. */
static int run(double off_i, double off_q, double slope_i, double slope_q, double sigma, double wander,
               unsigned periods, dc_servo_t *s, int *code_out)
{
    int code[2] = {0, 0}, dc[2] = {0, 0};
    for (unsigned p = 0; p < periods; ++p) {
        const double w = wander * sin(p * 0.21);
        const double ti = off_i + w + code[0] * slope_i / 1000., tq = off_q - w + code[1] * slope_q / 1000.;
        int64_t si, sq;
        measure(ti, tq, sigma, DC_SERVO_MIN_SAMPLES, &si, &sq);
        if (!dc_servo_mean(si, sq, DC_SERVO_MIN_SAMPLES, dc)) return 99999;
        int step[2];
        dc_servo_decide(s, 83u, dc, step);
        if (step[0] || step[1]) {
            code[0] += step[0];
            code[1] += step[1];
            dc_servo_applied(s, 83u, dc, step);
        }
    }
    if (code_out) { code_out[0] = code[0]; code_out[1] = code[1]; }
    const double ti = off_i + code[0] * slope_i / 1000., tq = off_q + code[1] * slope_q / 1000.;
    const double e = fabs(ti) > fabs(tq) ? fabs(ti) : fabs(tq);
    return (int)(e * 1000.);
}

int main(void)
{
    dc_servo_t s;
    int dc[2];
    check("too few samples: no estimate", !dc_servo_mean(0, 0, DC_SERVO_MIN_SAMPLES - 1u, dc));
    check("mean in mcells (dsp units are twice a cell)",
          dc_servo_mean(3 * (int64_t)DC_SERVO_MIN_SAMPLES, -(int64_t)DC_SERVO_MIN_SAMPLES, DC_SERVO_MIN_SAMPLES, dc) &&
          dc[0] == 1500 && dc[1] == -500);

    /* The baseline boots: 1-4 cells off, noise 1.9 cells, slopes 50-250
     * mcells per code of either polarity. */
    static const double offs[][2] = {{-1.37, .21}, {1.18, 1.72}, {2.22, 1.97}, {1.54, -3.30}, {2.28, -1.73},
                                     {.49, -3.36}, {-3.9, -3.5}, {4.0, 4.0}};
    static const double slopes[][2] = {{150, 150}, {246, 127}, {-130, 220}, {60, -200}, {-250, -50}};
    int worst = 0;
    unsigned cases = 0;
    for (unsigned o = 0; o < sizeof(offs) / sizeof(offs[0]); ++o)
        for (unsigned k = 0; k < sizeof(slopes) / sizeof(slopes[0]); ++k) {
            dc_servo_init(&s);
            const int e = run(offs[o][0], offs[o][1], slopes[k][0], slopes[k][1], 1.9, 0., 20, &s, NULL);
            worst = e > worst ? e : worst;
            ++cases;
        }
    printf("     boot offsets x slopes: %u cases, worst |DC| after 20 periods %d mcells\n", cases, worst);
    check("every boot offset, either polarity: below 0.35 cells within 20 periods (5 s)", worst <= 350);

    /* Weak noise (sigma 0.8) with a 3.5-cell DC: the cloud sits on a rail. */
    dc_servo_init(&s);
    const int clipped = run(3.5, -3.6, 180, 140, .8, 0., 20, &s, NULL);
    check("clipped cloud (3.5 cells, sigma 0.8): converges anyway", clipped <= 350);

    /* Wandering DC (+-1 cell over ~30 periods): tracked. */
    dc_servo_init(&s);
    const int tracked = run(2.0, -1.0, 150, 150, 1.9, 1.0, 60, &s, NULL);
    check("wander of +-1 cell: tracked within 0.5 cells", tracked <= 500);

    /* Already centred: no steps (no hunting on noise). */
    dc_servo_init(&s);
    int code[2];
    (void)run(.05, -.08, 150, 150, 1.9, 0., 40, &s, code);
    check("centred: no hunting in 40 periods", s.steps == 0 && code[0] == 0 && code[1] == 0);

    /* A wrong default polarity is relearned. */
    dc_servo_init(&s);
    const int flipped = run(2.5, 2.5, -150, -150, 1.9, 0., 25, &s, NULL);
    check("inverted DAC polarity: learned, converges", flipped <= 350 && s.learned > 0);

    /* Steps never exceed the DAC trial limit. */
    dc_servo_init(&s);
    int big[2] = {9000, -9000}, step[2];
    dc_servo_decide(&s, 83u, big, step);
    check("step clamped to DC_SERVO_STEP_MAX", abs(step[0]) == DC_SERVO_STEP_MAX && abs(step[1]) == DC_SERVO_STEP_MAX);
    int small[2] = {200, -240};
    dc_servo_decide(&s, 83u, small, step);
    check("inside the deadband: no step", step[0] == 0 && step[1] == 0);

    /* A DC jump that is not the step's effect (a channel round): forgotten
     * by the caller, or, if not, a measured slope does not flip sign. */
    dc_servo_init(&s);
    (void)run(2.0, 2.0, 400, 400, 1.9, 0., 12, &s, NULL);   /* slopes measured, ~+400 */
    const int learned_i = s.slope[0], learned_q = s.slope[1];
    int d0[2] = {1500, 1500}, st0[2], st1[2];
    dc_servo_decide(&s, 83u, d0, st0);
    dc_servo_applied(&s, 83u, d0, st0);
    int jump[2] = {1500 + 600, 1500 + 700};            /* new channel: unrelated jump against the step */
    dc_servo_decide(&s, 83u, jump, st1);
    check("unrelated DC jump: a measured slope keeps its sign", s.known[0] && s.known[1] && s.slope[0] > 0 &&
          s.slope[1] > 0 && s.sign_rejects >= 1u && learned_i > 0 && learned_q > 0);
    dc_servo_init(&s);
    dc_servo_decide(&s, 83u, d0, st0);
    dc_servo_applied(&s, 83u, d0, st0);
    dc_servo_forget_step(&s, 83u);
    dc_servo_decide(&s, 83u, jump, st1);
    check("forgotten step: no slope evidence taken", s.slope[0] == DC_SERVO_SLOPE_DEFAULT &&
          s.slope[1] == DC_SERVO_SLOPE_DEFAULT && s.forgotten == 1u);
    /* Two rows with different offsets (board: G83 0.3 cells, G82 3.9 after
     * a channel round), Direct Gain alternating faster than one mean: each
     * row's sum only grows during its own visits, and both converge. */
    {
        dc_servo_t t;
        dc_servo_init(&t);
        const double off[2][2] = {{.3, -.2}, {1.1, -3.9}};
        int code[2][2] = {{0, 0}, {0, 0}};
        int64_t acc[2][2] = {{0, 0}, {0, 0}};
        uint32_t n[2] = {0, 0};
        for (unsigned visit = 0; visit < 400; ++visit) {
            const unsigned r = visit % 3u ? 0u : 1u;          /* 2:1 between G83 and G82 */
            int64_t si, sq;
            measure(off[r][0] + code[r][0] * .4, off[r][1] + code[r][1] * .4, 1.9, 8000u, &si, &sq);
            acc[r][0] += si; acc[r][1] += sq; n[r] += 8000u;   /* a short visit: 8000 samples */
            int dc[2], step[2];
            if (!dc_servo_mean(acc[r][0], acc[r][1], n[r], dc)) continue;
            acc[r][0] = acc[r][1] = 0; n[r] = 0;
            dc_servo_decide(&t, r ? 82u : 83u, dc, step);
            if (step[0] || step[1]) {
                code[r][0] += step[0]; code[r][1] += step[1];
                dc_servo_applied(&t, r ? 82u : 83u, dc, step);
            }
        }
        double worst = 0;
        for (unsigned r = 0; r < 2; ++r)
            for (unsigned a = 0; a < 2; ++a) {
                const double e = fabs(off[r][a] + code[r][a] * .4);
                worst = e > worst ? e : worst;
            }
        printf("     rows G83/G82 alternating: worst |DC| %.2f cells\n", worst);
        check("alternating rows with different offsets: both below 0.35 cells", worst <= .35);
    }
    printf("%u/%u dc servo checks passed\n", s_run - s_fail, s_run);
    return s_fail != 0;
}
