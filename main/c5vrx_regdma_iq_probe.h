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
    bool requested;
    bool restart_sequence_proven;
    bool chain_constructed;
    bool etm_feedback_enabled;
    uint32_t starts;
    uint32_t setup_failures;
    uint32_t flow_errors;
    uint32_t physical_rearms;
    uint32_t runtime_failures;
    uint32_t interrupt_raw;
    uint32_t current_link;
    uint32_t peripheral_address;
    uint32_t memory_address;
    uint32_t target_control_register;
    uint32_t done_mask;
    uint32_t start_mask;
} c5vrx_regdma_iq_probe_status_t;

/* Read-only eligibility report.  No PAU clock, descriptor, ETM or RF register
 * is modified until the minimal restart sequence has physical proof. */
esp_err_t c5vrx_regdma_iq_probe_get_status(
    c5vrx_regdma_iq_probe_status_t *status);

/* Prepare one bounded hardware experiment. LP observes the proven RF DONE
 * boundary and gives PAU one start; REGDMA performs the four modem writes. */
esp_err_t c5vrx_regdma_iq_probe_arm(void);
void c5vrx_regdma_iq_probe_note_result(uint32_t rearms,
                                       uint32_t failures);
void c5vrx_regdma_iq_probe_note_diagnostics(uint32_t conf,
                                            uint32_t interrupt_raw,
                                            uint32_t current_link,
                                            uint32_t peripheral_address,
                                            uint32_t memory_address);
void c5vrx_regdma_iq_probe_set_requested(bool requested);
bool c5vrx_regdma_iq_probe_requested(void);
