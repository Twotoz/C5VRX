/* C5VRX by Twotoz and contributors. Bounded video level servo. */
#pragma once
#include "cvbs_monitor.h"
typedef struct {
    unsigned good, updates, refusals;
    int blank, span;
    uint32_t context;
    uint64_t last_us;
    uint64_t observed_us;
    bool context_valid;
    bool observed_valid;
    uint8_t codes[256];
} c5v4_level_t;
void c5v4_level_init(c5v4_level_t *state);
/* False freezes the last applied mapping, including through signal loss. */
bool c5v4_level_observe(c5v4_level_t *state, const c5v4_cvbs_stats_t *stats,
                      bool fresh, uint32_t context, uint64_t now_us);
uint16_t c5v4_level_word(uint16_t original, unsigned code);
/* One adjacent loaded-voltage step toward target, independent of code order. */
uint8_t c5v4_level_slew(uint8_t current, uint8_t target, const uint32_t uv[64]);
