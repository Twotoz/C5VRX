#pragma once

#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define ALPHA_FM_BLOCK_BYTES 16384u
#define ALPHA_FM_HALF_PERIOD_US 410u
#define ALPHA_BOUNDARY_REPAIR_MAX_PAIRS 512u
#define ALPHA_INITIAL_STATE 2u

typedef struct {
    uint32_t runs;
    uint32_t failures;
    uint32_t short_writes;
    uint32_t deadline_misses;
    uint32_t last_m2m_us;
    uint32_t max_m2m_us;
    uint32_t last_us;
    uint32_t max_us;
    uint32_t last_bytes_written;
    uint32_t boundary_repair_pairs;
    uint32_t max_boundary_repair_pairs;
    uint32_t state_convergence_misses;
} alpha_fm_stats_t;

/*
 * Alpha is the predictive companion to exact-adjacent Phase5:
 *
 * raw Q4/I4 -> phase5 every sample -> exact d0+d1 (no re-wrap)
 *            -> confidence-aware 8-state predictive tracker -> [D,D] CVBS.
 *
 * High-confidence observations pass through exactly. Low-confidence
 * observations are innovation-limited against the previous accepted output
 * state before the sample is emitted.
 */
esp_err_t alpha_fm_init(void);
void alpha_fm_deinit(void);

esp_err_t alpha_fm_transform(const uint8_t *input, uint8_t *output,
                             size_t bytes, uint8_t previous_raw);

const alpha_fm_stats_t *alpha_fm_stats(void);

uint8_t alpha_fm_reference_pair(uint8_t previous_raw,
                                uint8_t sample0,
                                uint8_t sample1,
                                uint8_t state,
                                uint8_t *next_state);

#ifdef __cplusplus
}
#endif
