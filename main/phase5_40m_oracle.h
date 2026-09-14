#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Run the Phase5 @ 40M RX-BitScrambler Premapper Oracle.
 *
 * Validates:
 * 1. Loopback byte-exact match of 1-bundle raw Q4/I4 -> 5-bit atan2 phase mapper.
 * 2. Physical hardware PARLIO RX + attached RX BitScrambler streaming from MODEM_DIAG.
 * 3. 40,000,000 bytes/s continuous throughput without RX FIFO overflow, gaps, or rearms.
 *
 * @return ESP_OK on all gates passing, or error code.
 */
esp_err_t c5vrx2_phase5_40m_oracle_run(void);

#ifdef __cplusplus
}
#endif
