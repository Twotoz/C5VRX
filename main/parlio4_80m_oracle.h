#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Run the Stage 1/2 PARLIO 4-bit @ 80 MHz hardware transport and grouped-DAC oracle.
 *
 * Validates:
 * 1. Direct TX40 baseline control (40 MB/s, 8-bit, no FIFO empty).
 * 2. PARLIO4 @ 80 MHz timed one-shot transfer (measured ~40 MB/s upstream, ~80 MS/s pad cadence).
 * 3. Sticky tx_fifo_rempty flag verification during sustained continuous loop transmission.
 * 4. Loopback capture & nibble-exact cyclic verification (0 errors out of 4000 samples).
 * 5. GPIO matrix concurrent fanout verification (GPIO 23 mirrors GPIO 8, GPIO 24 mirrors GPIO 9).
 * 6. 4-bit grouped DAC monotonicity and linearity ([0, 4, 8, 12, 17, 21, 25, 29, 34, 38, 42, 46, 51, 55, 59, 63]).
 * 7. Results persisted into 'diagcap' partition for offline verification.
 */
esp_err_t c5vrx2_parlio4_80m_oracle_run(void);

#ifdef __cplusplus
}
#endif
