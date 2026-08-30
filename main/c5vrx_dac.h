/* SPDX-License-Identifier: GPL-3.0-only */
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define C5VRX_DAC_CODES 64u
#define C5VRX_DAC_SYNC_CODE 0u
#define C5VRX_DAC_BLANK_CODE 18u
#define C5VRX_DAC_BLACK_CODE 18u
#define C5VRX_DAC_WHITE_CODE 62u

/* Loaded output voltage for the exact XIAO D4..D9 ladder, in microvolts.
 * Model: 3.3-V GPIOs, 8.2k/3.9k/2.0k/1.0k/470R/240R branches,
 * 200R shunt and a 75R terminated video input. */
extern const uint32_t c5vrx_dac_voltage_uv[C5VRX_DAC_CODES];

uint8_t c5vrx_dac_nearest_code_uv(uint32_t target_uv);
uint8_t c5vrx_dac_nearest_code_mv(uint32_t target_mv);

#ifdef __cplusplus
}
#endif
