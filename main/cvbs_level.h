/* C5VRX by Twotoz and contributors. Adapted from C5VRX-4 PR #162.
 * Golden voltage-domain servo; no RF actuator or sync regeneration. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef struct {
    bool valid;
    unsigned repeated, period, origin_pm, clip_pm;
    int sync_uv, blank_uv, sync_mad_uv, blank_mad_uv;
} cvbs_level_stats_t;
typedef struct {
    uint8_t codes[64];
    unsigned good, updates, refusals;
    int blank, span;
    uint64_t observed_us, last_us;
    uint32_t context;
    bool observed, have_context;
} cvbs_level_t;
extern const uint32_t cvbs_dac_uv[64];
void cvbs_level_init(cvbs_level_t *s);
uint8_t cvbs_level_slew(uint8_t current, uint8_t target, const uint32_t uv[64]);
bool cvbs_level_observe(cvbs_level_t *s, const cvbs_level_stats_t *v,
                        bool fresh, uint32_t context, uint64_t now);
/* Consumes immutable IQ copy in place; original Golden LUT, not applied map.
 * Local odd endpoint alignment is semantic evidence, not tagged TX output. */
void cvbs_level_analyze(uint8_t *raw, size_t n, const uint16_t lut[1024],
                        cvbs_level_stats_t *out);
