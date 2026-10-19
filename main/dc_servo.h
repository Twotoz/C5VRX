/* C5VRX-5 by Twotoz and the C5VRX contributors: live DC servo decisions
 * (pure logic, host test test/test_dc_servo.c; actuator in calibration.c).
 *
 * Every channel setup gives the receiver DC another offset: six boots with
 * the VTX off left 1.0-3.8 cells after 60 s, a range limit of 2-8 dB C/N
 * against 2-3 dB without the DC (research/sensitivity_model.py, 2026-10-19),
 * and within a boot the DC wanders by 1-2 cells over seconds. The former trim
 * tried single steps on 0.1-0.9 s samples, refused clipped windows, and hunted
 * in its deadband; it corrected next to nothing.
 *
 * The servo measures the DC as the mean of every observer probe of one gain
 * row and context (the AGC already sums I and Q per probe, gain.c), decides
 * once per period on tens of thousands of samples, and steps both DAC codes
 * at once by the learned slope (mcells per code, signed, per axis). A carrier
 * and the noise average out over that many probes. Clipping biases the mean
 * towards zero but never across it, so the loop still converges to the true
 * zero, where the clipping bias vanishes. A step that did not help twice
 * running makes the axis relearn its slope from a small probe step. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

#define DC_SERVO_MIN_SAMPLES   40000u   /* ~1 ms of IQ, spread over >= 150 probes */
#define DC_SERVO_DEADBAND      250      /* mcells: ~0.13 of the noise per axis at the edge */
#define DC_SERVO_GAIN_PCT      70       /* fraction of the computed step (no overshoot on slope error) */
#define DC_SERVO_STEP_MAX      8        /* codes per axis and period (phy_dco_try_pair) */
#define DC_SERVO_SLOPE_DEFAULT 150      /* mcells per code until learned (board: 50-250) */
#define DC_SERVO_SLOPE_MIN     30
#define DC_SERVO_SLOPE_MAX     800
#define DC_SERVO_PROBE         2        /* codes, to learn a slope */

typedef struct {
    int slope[2];       /* mcells per code, signed; 0 = unknown (probe first) */
    int last_dc[2];     /* DC before the last step */
    int last_step[2];   /* codes of the last step, 0 = none pending */
    uint8_t bad[2];     /* consecutive steps that did not reduce |DC| */
    uint32_t steps, learned, relearned;
} dc_servo_t;

static inline int dc_servo_abs(int v) { return v < 0 ? -v : v; }

static inline void dc_servo_init(dc_servo_t *s)
{
    *s = (dc_servo_t){{DC_SERVO_SLOPE_DEFAULT, DC_SERVO_SLOPE_DEFAULT}, {0, 0}, {0, 0}, {0, 0}, 0, 0, 0};
}

/* Mean of the accumulated probes in mcells. Sums are in dsp units (2v+1,
 * twice a cell), so mcells = sum * 500 / n. */
static inline bool dc_servo_mean(int64_t sum_i, int64_t sum_q, uint32_t samples, int dc[2])
{
    if (samples < DC_SERVO_MIN_SAMPLES) return false;
    dc[0] = (int)(sum_i * 500 / (int64_t)samples);
    dc[1] = (int)(sum_q * 500 / (int64_t)samples);
    return true;
}

/* One decision on a fresh mean measured after the last step (same row and
 * context). Learns the slope from the last step's effect, then returns the
 * next step per axis (0 = none). */
static inline void dc_servo_decide(dc_servo_t *s, const int dc[2], int step[2])
{
    for (int a = 0; a < 2; ++a) {
        step[a] = 0;
        if (s->last_step[a]) {
            const int moved = dc[a] - s->last_dc[a];
            const int slope = moved / s->last_step[a];
            if (dc_servo_abs(moved) >= 150 && dc_servo_abs(slope) >= DC_SERVO_SLOPE_MIN &&
                dc_servo_abs(slope) <= DC_SERVO_SLOPE_MAX) {
                /* Smooth a known slope; take the first measurement as is. */
                s->slope[a] = s->slope[a] && (s->slope[a] > 0) == (slope > 0) ? (s->slope[a] + slope) / 2 : slope;
                ++s->learned;
            }
            if (dc_servo_abs(dc[a]) >= dc_servo_abs(s->last_dc[a]) && dc_servo_abs(dc[a]) > DC_SERVO_DEADBAND) {
                if (++s->bad[a] >= 2u) { s->slope[a] = 0; s->bad[a] = 0; ++s->relearned; }
            } else {
                s->bad[a] = 0;
            }
            s->last_step[a] = 0;
        }
        if (dc_servo_abs(dc[a]) <= DC_SERVO_DEADBAND) continue;
        int st;
        if (!s->slope[a]) {
            st = DC_SERVO_PROBE;                    /* unknown polarity: probe and learn */
        } else {
            const long num = -(long)dc[a] * DC_SERVO_GAIN_PCT;
            const long den = (long)s->slope[a] * 100;
            long q = num / den;                                     /* rounded to nearest */
            const long r = num % den;
            if (2 * (r < 0 ? -r : r) >= (den < 0 ? -den : den)) q += (num < 0) != (den < 0) ? -1 : 1;
            st = (int)q;
            if (!st) st = (dc[a] > 0) == (s->slope[a] > 0) ? -1 : 1;
        }
        if (st > DC_SERVO_STEP_MAX) st = DC_SERVO_STEP_MAX;
        if (st < -DC_SERVO_STEP_MAX) st = -DC_SERVO_STEP_MAX;
        step[a] = st;
    }
}

/* The step was written: remember it for the slope and the check. */
static inline void dc_servo_applied(dc_servo_t *s, const int dc[2], const int step[2])
{
    for (int a = 0; a < 2; ++a) {
        s->last_dc[a] = dc[a];
        s->last_step[a] = step[a];
    }
    ++s->steps;
}

/* The row or context changed before the next mean: no slope evidence. */
static inline void dc_servo_forget_step(dc_servo_t *s)
{
    s->last_step[0] = s->last_step[1] = 0;
}
