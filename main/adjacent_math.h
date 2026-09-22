#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Host-testable arithmetic for the live adjacent Phase5 M2M kernel.
 *
 * One turn = 32 Phase5 states. Each adjacent difference is wrapped exactly
 * once into [-16,+15]. Two adjacent differences are added in a wider signed
 * integer and MUST NOT be wrapped a second time. */

static inline int adjacent_signed5(unsigned value)
{
    value &= 0x1fu;
    return (value & 0x10u) ? (int)value - 32 : (int)value;
}

static inline int adjacent_signed6(unsigned value)
{
    value &= 0x3fu;
    return (value & 0x20u) ? (int)value - 64 : (int)value;
}

static inline int adjacent_wrap_delta5(int delta)
{
    while (delta >= 16) delta -= 32;
    while (delta < -16) delta += 32;
    return delta;
}

static inline int adjacent_pair_sum5(unsigned previous,
                                     unsigned middle,
                                     unsigned current)
{
    int d0 = adjacent_wrap_delta5((int)middle - (int)previous);
    int d1 = adjacent_wrap_delta5((int)current - (int)middle);
    return d0 + d1; /* NO SECOND WRAP */
}

static inline bool adjacent_pair_proves_winding(int pair)
{
    return pair < -16 || pair >= 16;
}

static inline uint8_t adjacent_clamp_code(int value)
{
    if (value < 0) return 0u;
    if (value > 63) return 63u;
    return (uint8_t)value;
}

static inline uint8_t adjacent_map_pair_to_cvbs(int pair)
{
    /* One Phase5 step is approximately eight phase8 units. Golden's P20/G2
     * mapping applies 1.5x gain and the real 2:1 combine: 8 * 3 / 4 = 6. */
    return adjacent_clamp_code(20 + 6 * pair);
}
