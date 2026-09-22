#pragma once

#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define ADJACENT_FM_BLOCK_BYTES 16384u
#define ADJACENT_FM_HALF_PERIOD_US 410u

typedef struct {
    uint32_t runs;
    uint32_t failures;
    uint32_t short_writes;
    uint32_t deadline_misses;
    uint32_t last_us;
    uint32_t max_us;
    uint32_t last_bytes_written;
} adjacent_fm_stats_t;

/* Owns both C5 BitScrambler directions through the IDF M2M loopback driver. */
esp_err_t adjacent_fm_init(void);
void adjacent_fm_deinit(void);

/*
 * Transform one complete 16 KiB raw-Q4 half-ring into 16 KiB [D,D] CVBS.
 * previous_raw is the Q4/I4 byte immediately preceding input[0].
 *
 * The finite loopback transport has no guaranteed cross-run DSP state, so the
 * first output pair is repaired from previous_raw + input[0:2] after the
 * hardware transform. All following pairs are produced by the adjacent kernel.
 */
esp_err_t adjacent_fm_transform(const uint8_t *input, uint8_t *output,
                                size_t bytes, uint8_t previous_raw);

const adjacent_fm_stats_t *adjacent_fm_stats(void);
uint8_t adjacent_fm_reference_pair(uint8_t previous_raw,
                                   uint8_t sample0,
                                   uint8_t sample1);

#ifdef __cplusplus
}
#endif
