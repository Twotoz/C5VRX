/* C5VRX by Twotoz and contributors. Golden-only experimental LUT access. */
#pragma once
#include "cvbs_level.h"
void cvbs_level_hw_lock(void);
void cvbs_level_hw_unlock(void);
void cvbs_level_hw_stop(void);
bool cvbs_level_hw_prepare(void);
void cvbs_level_hw_analyze(uint8_t *raw, size_t n, cvbs_level_stats_t *out);
void cvbs_level_hw_observe(const cvbs_level_stats_t *v, bool fresh,
                           uint32_t context, uint64_t now);
void cvbs_level_hw_print(bool requested);
