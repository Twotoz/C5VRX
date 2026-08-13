#pragma once

#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Start a repeated PAL-line-like 8-bit test waveform through PARLIO.
 *
 * This is intentionally independent of the RF path. It exists to validate the
 * digital -> resistor-DAC -> 75 ohm CVBS output half of C5VRX before continuous
 * RF capture is solved.
 */
esp_err_t c5vrx_cvbs_test_start(void);

/** Stop the experimental PARLIO test output. */
esp_err_t c5vrx_cvbs_test_stop(void);

/** True while the PARLIO loop transmission is active. */
bool c5vrx_cvbs_test_running(void);

#ifdef __cplusplus
}
#endif
