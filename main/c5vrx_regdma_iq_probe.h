/* SPDX-License-Identifier: GPL-3.0-only */

#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

typedef struct {
    bool soc_supported;
    bool wait_node_supported;
    bool write_node_supported;
    bool etm_start_supported;
    bool active;
    bool restart_sequence_proven;
    uint32_t target_control_register;
    uint32_t done_mask;
    uint32_t start_mask;
} c5vrx_regdma_iq_probe_status_t;

/* Read-only eligibility report.  No PAU clock, descriptor, ETM or RF register
 * is modified until the minimal restart sequence has physical proof. */
esp_err_t c5vrx_regdma_iq_probe_get_status(
    c5vrx_regdma_iq_probe_status_t *status);
