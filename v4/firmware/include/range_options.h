#pragma once
#include <stdint.h>
/* Generated C5VRX by Twotoz/contributors. Pinned LAB; physical acceptance separate. */
#define C5VRX4_RANGE_OPTION_COUNT 6
typedef struct {
    const char *label, *model_id;
    uint8_t phase_bits;
    uint16_t phase_states, observation_tokens, frequency_states;
    uint8_t selectable;
} c5vrx4_range_option_t;
static const c5vrx4_range_option_t c5vrx4_range_options[] = {
    {"RANGE MAX LAB", "f96c6225fc10", 3, 8, 4, 32, 0},
    {"RANGE BAL LAB", "c2510e3dad79", 5, 32, 32, 1, 1},
    {"RANGE32+ LAB", "58c0e88fcba8", 5, 32, 32, 1, 0},
    {"PAIR RANGE LAB", "e2a8f30af45e", 5, 32, 32, 1, 1},
    {"EDGE RANGE LAB", "61ba9157643c", 2, 4, 8, 32, 1},
    {"OMEGA BELIEF LAB", "249d670670f5", 0, 0, 8, 0, 0},
};
