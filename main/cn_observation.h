/* C5VRX by Twotoz/contributors: slow C/N, fast gain, explicit freshness. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#define CN_OBSERVE_PERIOD_US 20000u
#define CN_OBSERVE_MAX_AGE_US 40000u
/* Do not cache transition AM as carrier noise. The gain owner must also
 * have finished its measured settling state, which may exceed this floor. */
#define CN_GAIN_SETTLE_US 500u
static inline bool cn_gain_settled(uint64_t now, uint64_t written, bool settling)
{
    return !settling && now >= written && now - written >= CN_GAIN_SETTLE_US;
}
typedef struct {
    uint32_t gain, control, phy, table;
    uint64_t us;
    int ratio;
} cn_observation_t;
static inline bool cn_observation_fresh(const cn_observation_t *o, uint64_t now,
                                        uint32_t gain, uint32_t control, uint32_t phy, uint32_t table)
{
    return o->us && now >= o->us && now - o->us <= CN_OBSERVE_MAX_AGE_US &&
           o->gain == gain && o->control == control && o->phy == phy && o->table == table;
}
