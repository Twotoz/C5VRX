/* C5VRX by Twotoz and contributors. Bounded video level servo. */
#pragma once
#include "cvbs_monitor.h"
#define C5V4_LEVEL_PERIOD_US 20000u
#define C5V4_LEVEL_STEP_CODES 2u
typedef struct {
    unsigned good, updates, refusals;
    int blank, span;
    uint32_t context;
    uint64_t last_us;
    uint64_t evidence_us;
    int evidence_blank[3], evidence_span[3];
    unsigned target_depth_mv;
    unsigned evidence_slot, evidence_depth;
    bool context_valid;
    uint8_t codes[256];
} c5v4_level_t;
void c5v4_level_init(c5v4_level_t *state);
/* Start slewing from the DAC codes actually loaded (STD150 or CVBS150), not
 * from the default table: every update is slew limited. */
void c5v4_level_seed(c5v4_level_t *state, const uint8_t codes[256]);
/* False freezes the last applied mapping, including through signal loss. */
bool c5v4_level_observe(c5v4_level_t *state, const c5v4_cvbs_stats_t *stats,
                      bool fresh, uint32_t context, uint64_t now_us);
uint16_t c5v4_level_word(uint16_t original, unsigned code);
