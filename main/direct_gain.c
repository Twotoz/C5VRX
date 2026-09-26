#include "direct_gain.h"
#include <string.h>

#define DIRECT_GAIN_TARGET_P      22
#define DIRECT_GAIN_DEADBAND_LO   19
#define DIRECT_GAIN_DEADBAND_HI   25
#define DIRECT_GAIN_SETTLE_TICKS  1u
#define DIRECT_GAIN_MAX_SLEW_UP   4u   /* The sole slew limit for Direct Gain */
#define DIRECT_GAIN_MAX_SLEW_DOWN 6u

/* Inverse-Q4 Gain Transfer LUT:
 * Precomputed delta-gain steps required to steer P_median from current P to sweet spot P_target (~22).
 * P is I^2+Q^2: the required correction is 10*log10(22/P)/0.82,
 * NOT 20*log10(22/P)/0.82. Index spacing is only a local heuristic;
 * vendor RF tuples are not globally monotonic. Values rounded to nearest.
 * Deadband [19..25] -> 0 steps (perfect envelope, zero hunting, instant HOLD).
 */
static const int8_t s_p_to_delta_gain[46] = {
    /*  0 */   0, /* cannot infer an amplitude from zero power */
    /*  1 */ +16,
    /*  2 */ +13,
    /*  3 */ +11,
    /*  4 */  +9,
    /*  5 */  +8,
    /*  6 */  +7,
    /*  7 */  +6,
    /*  8 */  +5,
    /*  9 */  +5,
    /* 10 */  +4,
    /* 11 */  +4,
    /* 12 */  +3,
    /* 13 */  +3,
    /* 14 */  +2,
    /* 15 */  +2,
    /* 16 */  +2,
    /* 17 */  +1,
    /* 18 */  +1,
    /* 19 */   0, /* DEADBAND SWEET SPOT START */
    /* 20 */   0,
    /* 21 */   0,
    /* 22 */   0, /* IDEAL TARGET */
    /* 23 */   0,
    /* 24 */   0,
    /* 25 */   0, /* DEADBAND SWEET SPOT END */
    /* 26 */  -1,
    /* 27 */  -1,
    /* 28 */  -1,
    /* 29 */  -1,
    /* 30 */  -2,
    /* 31 */  -2,
    /* 32 */  -2,
    /* 33 */  -2,
    /* 34 */  -2,
    /* 35 */  -2,
    /* 36 */  -3,
    /* 37 */  -3,
    /* 38 */  -3,
    /* 39 */  -3,
    /* 40 */  -3,
    /* 41 */  -3,
    /* 42 */  -3,
    /* 43 */  -4,
    /* 44 */  -4,
    /* 45 */  -4,
};

static uint8_t clamp_gain(const direct_gain_controller_t *dg, int gain)
{
    uint8_t min_gain = 2u;
    uint8_t max_gain = dg->table.max_index ? dg->table.max_index : 81u;
    if (gain < (int)min_gain) return min_gain;
    if (gain > (int)max_gain) return max_gain;
    return (uint8_t)gain;
}

void direct_gain_reset(direct_gain_controller_t *dg,
                       const arc_gain_table_t *table,
                       uint8_t initial_gain)
{
    if (!dg) return;
    memset(dg, 0, sizeof(*dg));
    if (table) dg->table = *table;
    dg->current_gain = clamp_gain(dg, initial_gain);
    dg->target_gain = dg->current_gain;
    dg->state = DIRECT_GAIN_SEEK;
    dg->settle_ticks = 0;
    dg->cal_offset_db = 0;
    dg->last_p = 0;
    dg->last_delta_gain = 0;
    dg->last_estimated_input_dbm = -127;
    dg->last_rssi_used = false;
    dg->no_carrier_ticks = 0;
    dg->total_writes = 0;
    dg->hold_cycles = 0;
}

uint8_t direct_gain_tick(direct_gain_controller_t *dg,
                         const direct_gain_observation_t *obs)
{
    if (!dg || !obs) return 52u;

    int p = obs->p_median;
    if (p < 0) p = 0;
    dg->last_p = p;

    /* Classify loss of carrier before interpreting noise/outer-bin occupancy
     * as an overload or a request for more gain. Never learn on these windows. */
    if (obs->q_phase < 18 && obs->origin_permille >= 650) {
        if (dg->no_carrier_ticks < 255u) ++dg->no_carrier_ticks;
        dg->cal_offset_db = 0;
        dg->settle_ticks = 0;
        dg->state = DIRECT_GAIN_SEEK;
        if (dg->no_carrier_ticks >= 3u && obs->survival_gain) {
            dg->target_gain = clamp_gain(dg, obs->survival_gain);
            dg->current_gain = dg->target_gain;
        }
        return dg->current_gain;
    }
    dg->no_carrier_ticks = 0;

    /* 1. Wait one ~50 ms control tick after a write. This is decision
     * settling only: it does not blank the live DAC waveform. */
    if (dg->settle_ticks > 0) {
        --dg->settle_ticks;
        if (dg->settle_ticks == 0) {
            /* Settle complete: evaluate accuracy of the single-write hop (Slow Path verification) */
            if (p >= DIRECT_GAIN_DEADBAND_LO && p <= DIRECT_GAIN_DEADBAND_HI &&
                obs->clip_permille < 20) {
                /* Target envelope achieved in 1 write! Enter HOLD. */
                dg->state = DIRECT_GAIN_HOLD;
                dg->hold_ticks = 1;
                ++dg->hold_cycles;
            } else if (p < DIRECT_GAIN_DEADBAND_LO - 3 && dg->cal_offset_db < 4) {
                /* Slight undershoot: tiny learned offset trim */
                dg->cal_offset_db += 1;
                dg->state = DIRECT_GAIN_SEEK;
            } else if (p > DIRECT_GAIN_DEADBAND_HI + 3 && dg->cal_offset_db > -4) {
                /* Slight overshoot: tiny learned offset trim */
                dg->cal_offset_db -= 1;
                dg->state = DIRECT_GAIN_SEEK;
            } else {
                dg->state = DIRECT_GAIN_HOLD;
            }
        }
        return dg->current_gain;
    }

    /* 2. QUALITY SEPARATION (Golden Rule)
     * Q_phase, origin_permille, and winding metrics represent signal quality/multipath,
     * NOT RF amplitude. If P is in the sweet spot, DO NOT TOUCH GAIN, even if Q is degraded! */
    if (p >= DIRECT_GAIN_DEADBAND_LO && p <= DIRECT_GAIN_DEADBAND_HI &&
        obs->clip_permille < 20) {
        dg->state = DIRECT_GAIN_HOLD;
        if (dg->hold_ticks < 65535u) ++dg->hold_ticks;
        return dg->current_gain;
    }

    /* 3. EMERGENCY OVERLOAD / CLIPPING PROTECTION
     * Rail saturation requires an immediate fast cut to restore linearity. */
    if (p > 44) {
        int cut = -14;
        uint8_t next = clamp_gain(dg, (int)dg->current_gain + cut);
        if (next != dg->current_gain) {
            dg->current_gain = next;
            dg->target_gain = next;
            dg->settle_ticks = DIRECT_GAIN_SETTLE_TICKS;
            dg->state = DIRECT_GAIN_SETTLE;
            dg->last_delta_gain = cut;
            ++dg->total_writes;
            return dg->current_gain;
        }
    } else if (obs->clip_permille >= 80 && p > 35) {
        int cut = -8;
        uint8_t next = clamp_gain(dg, (int)dg->current_gain + cut);
        if (next != dg->current_gain) {
            dg->current_gain = next;
            dg->target_gain = next;
            dg->settle_ticks = DIRECT_GAIN_SETTLE_TICKS;
            dg->state = DIRECT_GAIN_SETTLE;
            dg->last_delta_gain = cut;
            ++dg->total_writes;
            return dg->current_gain;
        }
    }

    /* 4. FEED-FORWARD RF LEVEL ESTIMATION & DIRECT GAIN TRANSFER
     * Estimate input deficit in dB via Inverse-Q4 LUT */
    int lut_idx = p > 45 ? 45 : p;
    int delta = (int)s_p_to_delta_gain[lut_idx] + (int)dg->cal_offset_db;

    /* RSSI is telemetry only until forced-gain sweeps establish whether it
     * is pre-gain, monotonic and calibrated for this PHY/table. */
    bool carrier_authentic = obs->q_phase >= 50 && obs->origin_permille <= 350;
    if (obs->rssi_valid && obs->rssi_dbm >= -100 && obs->rssi_dbm <= -10 && carrier_authentic) {
        dg->last_rssi_used = false;
        dg->last_estimated_input_dbm = obs->rssi_dbm;
    } else {
        dg->last_rssi_used = false;
        /* Estimate input power: known gain + current P */
        dg->last_estimated_input_dbm = -90 + (int)dg->current_gain - (DIRECT_GAIN_TARGET_P - p);
    }

    dg->last_delta_gain = delta;

    /* 5. TARGET GAIN ESTIMATION & SMOOTH SLEW-RATE HOP EXECUTION
     * When tracking an active carrier (p >= 8 or carrier authentic), we cap the
     * gain step size to +5 / -6 steps per tick. This produces seamless, butter-smooth
     * AGC ramping with zero video flicker or DC shifts.
     * When carrier is absent (p == 0, cold start or channel switch), a full 1-hop write
     * is permitted for instantaneous signal lock! */
    if (delta != 0) {
        uint8_t desired_target = clamp_gain(dg, (int)dg->current_gain + delta);
        dg->target_gain = desired_target;

        int step = (int)desired_target - (int)dg->current_gain;
        bool is_tracking = (p >= 8) || carrier_authentic || (dg->hold_ticks > 0);

        if (is_tracking) {
            if (step > (int)DIRECT_GAIN_MAX_SLEW_UP)   step = (int)DIRECT_GAIN_MAX_SLEW_UP;
            if (step < -(int)DIRECT_GAIN_MAX_SLEW_DOWN) step = -(int)DIRECT_GAIN_MAX_SLEW_DOWN;
        }

        uint8_t next_gain = clamp_gain(dg, (int)dg->current_gain + step);
        if (next_gain != dg->current_gain) {
            dg->current_gain = next_gain;
            dg->settle_ticks = DIRECT_GAIN_SETTLE_TICKS;
            dg->state = DIRECT_GAIN_SETTLE;
            dg->hold_ticks = 0;
            ++dg->total_writes;
        } else {
            dg->state = DIRECT_GAIN_HOLD;
            if (dg->hold_ticks < 65535u) ++dg->hold_ticks;
        }
    } else {
        dg->state = DIRECT_GAIN_HOLD;
        if (dg->hold_ticks < 65535u) ++dg->hold_ticks;
    }

    return dg->current_gain;
}

void direct_gain_sync_applied(direct_gain_controller_t *dg, uint8_t applied_gain)
{
    if (!dg) return;
    applied_gain = clamp_gain(dg, applied_gain);
    if (dg->current_gain != applied_gain) {
        dg->current_gain = applied_gain;
        dg->target_gain = applied_gain;
        dg->settle_ticks = DIRECT_GAIN_SETTLE_TICKS;
        dg->state = DIRECT_GAIN_SETTLE;
    }
}

const char *direct_gain_state_name(direct_gain_state_t state)
{
    switch (state) {
    case DIRECT_GAIN_HOLD:   return "HOLD";
    case DIRECT_GAIN_SETTLE: return "SETTLE";
    case DIRECT_GAIN_SEEK:   return "SEEK";
    default:                 return "UNKNOWN";
    }
}
