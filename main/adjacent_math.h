#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Pure host-testable arithmetic shared by the exact-adjacent software oracle.
 * Phase7 uses one full turn = 128. Each adjacent interval is wrapped once;
 * the two adjacent intervals are then added in a wider integer and MUST NOT
 * be wrapped a second time. */

static inline int adjacent_signed7(unsigned value)
{
    value &= 0x7fu;
    return (value & 0x40u) ? (int)value - 128 : (int)value;
}

static inline int adjacent_wrap_delta7(int delta)
{
    while (delta >= 64) delta -= 128;
    while (delta < -64) delta += 128;
    return delta;
}

static inline int adjacent_arshift1(int value)
{
    return value >= 0 ? value / 2 : -(((-value) + 1) / 2);
}

static inline int adjacent_pair_sum7(unsigned previous,
                                     unsigned middle,
                                     unsigned current)
{
    int d0 = adjacent_wrap_delta7((int)middle - (int)previous);
    int d1 = adjacent_wrap_delta7((int)current - (int)middle);
    return d0 + d1; /* NO SECOND WRAP */
}

static inline int adjacent_pair_qsum7(int pair)
{
    return adjacent_arshift1(pair);
}

static inline bool adjacent_pair_proves_winding(int pair)
{
    int qsum = adjacent_pair_qsum7(pair);
    return qsum < -32 || qsum >= 32;
}

static inline uint8_t adjacent_clamp_code(int value)
{
    if (value < 0) return 0u;
    if (value > 63) return 63u;
    return (uint8_t)value;
}

static inline uint8_t adjacent_map_qsum_to_cvbs(int qsum)
{
    return adjacent_clamp_code(20 + 3 * qsum);
}
