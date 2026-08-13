#pragma once

#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Start the analog-output proof generator.
 *
 * The generator streams a PAL 625/50 interlaced monochrome test raster at
 * 20 MS/s through PARLIO. It includes full horizontal/vertical sync structure,
 * grayscale bars and, when enabled in Kconfig, a PAL-frequency swinging burst
 * used only to stress analog bandwidth/locking.
 *
 * This is intentionally independent from the RF receive path so the
 * C5 -> PARLIO -> passive DAC -> 75-ohm CVBS half can be validated first.
 */
esp_err_t c5vrx_cvbs_test_start(void);

/** Stop the PAL PARLIO test stream. */
esp_err_t c5vrx_cvbs_test_stop(void);

/** True while the PAL test stream is active. */
bool c5vrx_cvbs_test_running(void);

#ifdef __cplusplus
}
#endif
