#include "direct_gain.h"
#include <string.h>

#define DIRECT_GAIN_TARGET_P      22
#define DIRECT_GAIN_DEADBAND_LO   19
#define DIRECT_GAIN_DEADBAND_HI   25
#define DIRECT_GAIN_SETTLE_TICKS  1u

/* Inverse-Q4 Gain Transfer LUT:
 * Precomputed delta-gain steps required to steer P_median from current P to sweet spot P_target (~22).
 * 1 step on ESP32-C5 is approximately ~0.82 dB.
 * Deadband [19..25] -> 0 steps (perfect envelope, zero hunting, instant HOLD).
 */
static const int8_t s_p_to_delta_gain[46] = {
    /*  0 */ +35, /* no signal / complete starvation */
    /*  1 */ +33,
    /*  2 */ +25,
    /*  3 */ +21,
    /*  4 */ +18,
    /*  5 */ +16,
    /*  6 */ +14,
    /*  7 */ +12,
    /*  8 */ +11,
    /*  9 */  +9,
    /* 10 */  +8,
    /* 11 */  +7,
    /* 12 */  +6,
    /* 13 */  +6,
    /* 14 */  +5,
    /* 15 */  +4,
    /* 16 */  +3,
    /* 17 */  +3,
    /* 18 */  +2,
    /* 19 */   0, /* DEADBAND SWEET SPOT START */
    /* 20 */   0,
    /* 21 */   0,
    /* 22 */   0, /* IDEAL TARGET */
    /* 23 */   0,
    /* 24 */   0,
    /* 25 */   0, /* DEADBAND SWEET SPOT END */
    /* 26 */  -2,
    /* 27 */  -2,
    /* 28 */  -3,
    /* 29 */  -3,
    /* 30 */  -3,
    /* 31 */  -4,
    /* 32 */  -4,
    /* 33 */  -4,
    /* 34 */  -5,
    /* 35 */  -5,
    /* 36 */  -5,
    /* 37 */  -6,
    /* 38 */  -6,
    /* 39 */  -6,
    /* 40 */  -6,
    /* 41 */  -7,
    /* 42 */  -7,
    /* 43 */  -7,
    /* 44 */  -7,
    /* 45 */  -8,
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

    /* 1. SHORT VERIFICATION / BLANKING WINDOW AFTER A WRITE
     * GDMA cyclic ring takes ~0.82 ms to flush. Settle ticks provides
     * a clean blanking window (~10-20 ms) before reading post-transition metrics. */
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
    if (obs->clip_permille >= 80 || p > 44) {
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
    } else if (obs->clip_permille >= 25) {
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

    /* Carrier Authenticator: Only trust hardware RSSI if the signal
     * demonstrates analog FM carrier coherence. This prevents nearby Wi-Fi
     * or adjacent-channel jammers from falsely driving gain down! */
    bool carrier_authentic = obs->q_phase >= 50 && obs->origin_permille <= 350;
    if (obs->rssi_valid && obs->rssi_dbm >= -100 && obs->rssi_dbm <= -10 && carrier_authentic) {
        dg->last_rssi_used = true;
        dg->last_estimated_input_dbm = obs->rssi_dbm;
        /* Hardware RSSI target gain estimation:
         * Standard sensitivity: -90 dBm -> ~G75, -30 dBm -> ~G22 */
        int rssi_target = -obs->rssi_dbm - 8;
        int rssi_delta = rssi_target - (int)dg->current_gain;
        /* If both estimates agree on direction, apply consensus */
        if ((delta > 0 && rssi_delta > 0) || (delta < 0 && rssi_delta < 0)) {
            delta = (delta + rssi_delta) / 2;
        }
    } else {
        dg->last_rssi_used = false;
        /* Estimate input power: known gain + current P */
        dg->last_estimated_input_dbm = -90 + (int)dg->current_gain - (DIRECT_GAIN_TARGET_P - p);
    }

    dg->last_delta_gain = delta;

    /* 5. SINGLE-WRITE HOP EXECUTION (1 Write G22 -> G73) */
    if (delta != 0) {
        uint8_t target = clamp_gain(dg, (int)dg->current_gain + delta);
        if (target != dg->current_gain) {
            dg->current_gain = target;
            dg->target_gain = target;
            dg->settle_ticks = DIRECT_GAIN_SETTLE_TICKS;
            dg->state = DIRECT_GAIN_SETTLE;
            dg->hold_ticks = 0;
            ++dg->total_writes;
        }
    } else {
        dg->state = DIRECT_GAIN_HOLD;
        if (dg->hold_ticks < 65535u) ++dg->hold_ticks;
    }

    return dg->current_gain;
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
