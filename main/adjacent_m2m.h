#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#define ADJACENT_M2M_BLOCK_BYTES 16368u /* 4 x 4092-byte RX GDMA descriptors */

typedef struct {
    uint32_t transforms;
    uint32_t failures;
    uint32_t short_writes;
    uint32_t last_us;
    uint32_t max_us;
    uint32_t held_boundary_pairs;
} adjacent_m2m_stats_t;

/* Claims the C5 BitScrambler in memory-to-memory loopback mode and loads the
 * full-Q4 adjacent-FM LUT. The live PARLIO TX path must be undecorated while
 * this object exists. */
esp_err_t adjacent_m2m_init(void);
void adjacent_m2m_deinit(void);

/* Transform one even-sized Q4/I4 block into an equal-sized [D,D] 40 MHz DAC
 * block. Every two input samples produce one 20 MS/s CVBS value duplicated
 * twice. */
esp_err_t adjacent_m2m_transform(const uint8_t *input, size_t input_bytes,
                                 uint8_t *output, size_t output_bytes,
                                 uint32_t *elapsed_us);

/* Software oracle for one exact adjacent pair. It preserves the no-second-wrap
 * pair sum and applies the same confidence-gated coarse holdover as hardware. */
uint8_t adjacent_m2m_reference_pair(uint8_t previous, uint8_t middle,
                                    uint8_t current, uint8_t previous_code,
                                    bool *held);

const adjacent_m2m_stats_t *adjacent_m2m_stats(void);
void adjacent_m2m_note_boundary_hold(void);
