/* SPDX-License-Identifier: GPL-3.0-only */
#include "c5vrx_dac.h"

#include <limits.h>

const uint32_t c5vrx_dac_voltage_uv[C5VRX_DAC_CODES] = {
         0,   15183,   31923,   47106,   62250,   77433,   94173,  109356,
    124500,  139683,  156423,  171606,  186750,  201933,  218673,  233856,
    264894,  280077,  296817,  312000,  327144,  342327,  359067,  374250,
    389394,  404577,  421317,  436500,  451644,  466827,  483567,  498750,
    518750,  533933,  550673,  565856,  581000,  596183,  612923,  628106,
    643250,  658433,  675173,  690356,  705500,  720683,  737423,  752606,
    783644,  798827,  815567,  830750,  845894,  861077,  877817,  893000,
    908144,  923327,  940067,  955250,  970394,  985577, 1002317, 1017500,
};

uint8_t c5vrx_dac_nearest_code_uv(uint32_t target_uv)
{
    uint32_t best_error = UINT_MAX;
    uint8_t best = 0;
    for (uint8_t code = 0; code < C5VRX_DAC_CODES; ++code) {
        const uint32_t voltage = c5vrx_dac_voltage_uv[code];
        const uint32_t error = voltage > target_uv ?
            voltage - target_uv : target_uv - voltage;
        if (error < best_error) {
            best_error = error;
            best = code;
        }
    }
    return best;
}

uint8_t c5vrx_dac_nearest_code_mv(uint32_t target_mv)
{
    return c5vrx_dac_nearest_code_uv(target_mv * 1000u);
}
