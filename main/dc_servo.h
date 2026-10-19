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
 * The servo measures each gain row's DC as the mean of every settled observer
 * probe taken in that row (the AGC already sums I and Q per probe, gain.c;
 * receiver.c keeps one sum per row), decides per row on tens of thousands of
 * samples, and steps both DAC codes of that row by the learned slope (mcells
 * per code, signed, per axis). Per row, because the stored codes are off by
 * different amounts per row after a channel setup (board 2026-10-19: G83 at
 * 0.3 cells, G82 at 3.9 after a channel round) and Direct Gain moves between
 * rows faster than one row collects a mean: short visits add up. A carrier
 * and the noise average out over that many probes. Clipping biases the mean
 * towards zero but never across it, so the loop still converges to the true
 * zero, where the clipping bias vanishes. A step that did not help twice
 * running makes the axis relearn its slope from a small probe step. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

#define DC_SERVO_ROWS          96u      /* >= ARC_VENDOR_GAIN_MAX + 1 */
#define DC_SERVO_MIN_SAMPLES   40000u   /* ~1 ms of IQ, spread over >= 150 probes */
#define DC_SERVO_DEADBAND      250      /* mcells: ~0.13 of the noise per axis at the edge */
#define DC_SERVO_GAIN_PCT      70       /* fraction of the computed step (no overshoot on slope error) */
#define DC_SERVO_STEP_MAX      8        /* codes per axis and step (phy_dco_try_pair) */
#define DC_SERVO_SLOPE_DEFAULT 150      /* mcells per code until learned (board: 170-550) */
#define DC_SERVO_SLOPE_MIN     30
#define DC_SERVO_SLOPE_MAX     800
#define DC_SERVO_PROBE         3        /* codes, to learn a slope */
/* The DC wanders by itself (flicker, ~0.3 cells between means), so a small
 * step's effect drowns in it: a slope is learned only from steps of at least
 * DC_SERVO_LEARN_STEP codes (~0.6-1.2 cells), and a step counts as failed
 * only while the DC stays above DC_SERVO_BAD_MCELLS, DC_SERVO_BAD_RUN times
 * running (board 2026-10-19: 32 relearns in 100 s with 2 / no floor). */
#define DC_SERVO_LEARN_STEP    3
#define DC_SERVO_BAD_MCELLS    800
#define DC_SERVO_BAD_RUN       3u

typedef struct {
    int slope[2];       /* mcells per code, signed, shared by the rows; 0 = unknown (probe first) */
    uint8_t known[2];   /* slope measured (not the default) */
    struct { int last_dc[2], last_step[2]; uint8_t bad[2]; } row[DC_SERVO_ROWS];
    uint32_t steps, learned, relearned, sign_rejects, forgotten;
} dc_servo_t;

static inline int dc_servo_abs(int v) { return v < 0 ? -v : v; }

static inline void dc_servo_init(dc_servo_t *s)
{
    *s = (dc_servo_t){0};
    s->slope[0] = s->slope[1] = DC_SERVO_SLOPE_DEFAULT;
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

/* One decision for one row on a fresh mean measured after that row's last
 * step (same codes and context). Learns the slope from the last step's
 * effect, then returns the next step per axis (0 = none). */
static inline void dc_servo_decide(dc_servo_t *s, unsigned r, const int dc[2], int step[2])
{
    step[0] = step[1] = 0;
    if (r >= DC_SERVO_ROWS) return;
    for (int a = 0; a < 2; ++a) {
        if (s->row[r].last_step[a]) {
            const int moved = dc[a] - s->row[r].last_dc[a];
            const int slope = moved / s->row[r].last_step[a];
            if (dc_servo_abs(s->row[r].last_step[a]) >= DC_SERVO_LEARN_STEP &&
                dc_servo_abs(moved) >= 150 && dc_servo_abs(slope) >= DC_SERVO_SLOPE_MIN &&
                dc_servo_abs(slope) <= DC_SERVO_SLOPE_MAX) {
                /* Smooth a known slope; take the first measurement as is. A
                 * known slope never flips sign on one measurement: the DAC
                 * polarity does not change, a DC jump of something else does
                 * (board 2026-10-19: a channel round taught -88 for +400 and
                 * left 3.6 cells). Repeated failure relearns it (bad). */
                if (!s->known[a]) { s->slope[a] = slope; s->known[a] = 1; ++s->learned; }
                else if ((s->slope[a] > 0) == (slope > 0)) { s->slope[a] = (s->slope[a] + slope) / 2; ++s->learned; }
                else ++s->sign_rejects;
            }
            if (dc_servo_abs(dc[a]) >= dc_servo_abs(s->row[r].last_dc[a]) && dc_servo_abs(dc[a]) > DC_SERVO_BAD_MCELLS) {
                if (++s->row[r].bad[a] >= DC_SERVO_BAD_RUN) {
                    s->slope[a] = 0; s->known[a] = 0; s->row[r].bad[a] = 0; ++s->relearned;
                }
            } else {
                s->row[r].bad[a] = 0;
            }
            s->row[r].last_step[a] = 0;
        }
        if (dc_servo_abs(dc[a]) <= DC_SERVO_DEADBAND) continue;
        int st;
        if (!s->slope[a]) {
            st = DC_SERVO_PROBE;                    /* unknown polarity: probe and learn */
        } else {
            const long num = -(long)dc[a] * DC_SERVO_GAIN_PCT;
            const long den = (long)s->slope[a] * 100;
            long q = num / den;                                     /* rounded to nearest */
            const long rem = num % den;
            if (2 * (rem < 0 ? -rem : rem) >= (den < 0 ? -den : den)) q += (num < 0) != (den < 0) ? -1 : 1;
            st = (int)q;
            if (!st) st = (dc[a] > 0) == (s->slope[a] > 0) ? -1 : 1;
        }
        if (st > DC_SERVO_STEP_MAX) st = DC_SERVO_STEP_MAX;
        if (st < -DC_SERVO_STEP_MAX) st = -DC_SERVO_STEP_MAX;
        step[a] = st;
    }
}

/* The row's step was applied: remember it for the slope and the check. */
static inline void dc_servo_applied(dc_servo_t *s, unsigned r, const int dc[2], const int step[2])
{
    if (r >= DC_SERVO_ROWS) return;
    for (int a = 0; a < 2; ++a) {
        s->row[r].last_dc[a] = dc[a];
        s->row[r].last_step[a] = step[a];
    }
    ++s->steps;
}

/* The row's context changed before its next mean: no slope evidence. */
static inline void dc_servo_forget_step(dc_servo_t *s, unsigned r)
{
    if (r >= DC_SERVO_ROWS) return;
    if (s->row[r].last_step[0] || s->row[r].last_step[1]) ++s->forgotten;
    s->row[r].last_step[0] = s->row[r].last_step[1] = 0;
}

static inline void dc_servo_forget_all(dc_servo_t *s)
{
    for (unsigned r = 0; r < DC_SERVO_ROWS; ++r) dc_servo_forget_step(s, r);
}
