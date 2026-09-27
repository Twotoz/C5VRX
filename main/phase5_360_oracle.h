#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "phase5_360_oracle_lut.h"

/* Full adjacent Phase5-domain reference for completed IQ windows only.
 * One 50 ns output integrates BOTH wrapped 25 ns steps without wrapping the
 * sum again. When there is no winding, preserve Golden's calibrated byte. */
static inline int phase5_360_wrap(int delta)
{
    return ((delta + 16) & 31) - 16;
}

static inline int phase5_360_winding(uint8_t previous, uint8_t middle,
                                     uint8_t current)
{
    int pair = phase5_360_wrap((int)middle - previous) +
               phase5_360_wrap((int)current - middle);
    return (pair - phase5_360_wrap((int)current - previous)) / 32;
}

/* One endpoint-conditioned bit is all the middle sample contributes to the
 * final discriminator. The comparisons deliberately have different equality
 * rules: ties at either antipode must match the -16/+15 wrap convention. */
static inline uint8_t phase5_360_middle_bit(uint8_t previous, uint8_t middle,
                                             uint8_t current)
{
    return (uint8_t)((middle >= (previous ^ 16u)) ^
                     (middle > (current ^ 16u)));
}

static inline uint8_t phase5_360_endpoint_bit(uint8_t previous, uint8_t current)
{
    return (uint8_t)(((current >> 4) & 1u) ^
                     (current >= (previous ^ 16u)));
}

static inline uint8_t phase5_360_route_bit(uint8_t previous, uint8_t middle,
                                            uint8_t current)
{
    return phase5_360_middle_bit(previous, middle, current) ^
           phase5_360_endpoint_bit(previous, current);
}

/* Raw IQ is fully consumed by the preprocessor; only one bit is handed to
 * the DAC decision. This reference runs on completed IQ windows, not in the
 * live 40 MS/s BitScrambler path. */
static inline uint8_t phase5_360_route_bit_raw(uint8_t previous,
                                                uint8_t middle_raw,
                                                uint8_t current)
{
    return phase5_360_route_bit(previous,
                                s_phase5_360_raw_phase[middle_raw], current);
}

static inline uint8_t phase5_360_dac_from_route_bit(uint8_t previous,
                                                     uint8_t current,
                                                     uint8_t route_bit)
{
    if (!route_bit)
        return s_phase5_360_golden_dac[((unsigned)previous << 5) | current];
    return phase5_360_wrap((int)current - previous) < 0 ? 63u : 0u;
}

static inline uint8_t phase5_360_adjacent_dac(uint8_t previous, uint8_t middle,
                                               uint8_t current)
{
    return phase5_360_dac_from_route_bit(
        previous, current, phase5_360_route_bit(previous, middle, current));
}

static inline uint8_t phase5_360_live_dac(uint8_t previous, uint8_t current)
{
    return s_phase5_360_live_dac[((unsigned)previous << 5) | current];
}
