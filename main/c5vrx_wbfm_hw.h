#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Run the C5 BitScrambler 4:1 WBFM discriminator against synthetic packed
 * 10-bit I/Q in RAM and compare the hardware result with a CPU reference.
 *
 * This validates the DSP transform itself on physical ESP32-C5 silicon. It
 * does not prove that the undocumented RF front-end can feed a continuous DMA
 * stream yet.
 */
esp_err_t c5vrx_wbfm_hw_self_test(void);

#ifdef __cplusplus
}
#endif
