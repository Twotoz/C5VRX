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

static inline uint8_t phase5_360_adjacent_dac(uint8_t previous, uint8_t middle,
                                               uint8_t current)
{
    int pair = phase5_360_wrap((int)middle - previous) +
               phase5_360_wrap((int)current - middle);
    if (pair == phase5_360_wrap((int)current - previous))
        return s_phase5_360_golden_dac[((unsigned)previous << 5) | current];

    int dac = 20 + 2 * pair;
    return (uint8_t)(dac < 0 ? 0 : dac > 63 ? 63 : dac);
}

static inline uint8_t phase5_360_live_dac(uint8_t previous, uint8_t current)
{
    return s_phase5_360_live_dac[((unsigned)previous << 5) | current];
}
