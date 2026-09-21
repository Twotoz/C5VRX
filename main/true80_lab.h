#pragma once

#include "esp_err.h"

/**
 * Run the bounded TRUE80 receive hardware oracle before the normal live
 * receiver starts.
 *
 * The probe is deliberately non-fatal: callers should log failures and still
 * start the proven 40 MS/s production path.
 *
 * Tests:
 *   1. PARLIO 80 MHz TX -> external-clock RX byte-exact loopback.
 *   2. MODEM_DIAG search for source-synchronous CLK80 and CLK40 lanes.
 *   3. Bounded Q4/I4 capture at the discovered native ~80 MHz clock on both
 *      PARLIO sample edges.
 *
 * All temporary GPIO/PARLIO routing is restored before this function returns.
 */
esp_err_t true80_lab_boot_probe(void);
