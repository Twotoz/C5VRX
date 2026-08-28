/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_regdma_iq_probe.h"

#include <string.h>
#include "esp_private/esp_pau.h"
#include "hal/pau_ll.h"
#include "soc/soc_caps.h"

#define C5_DUMP_CONTROL_REGISTER 0x600a9004u
#define C5_DUMP_DONE_MASK        0x00040000u
#define C5_DUMP_START_MASK       0x00080000u
#define C5_DUMP_ENABLE_MASK      0x80000000u

static uint32_t s_link_root;
static uint32_t s_starts;
static uint32_t s_setup_failures;
static uint32_t s_physical_rearms;
static uint32_t s_runtime_failures;
static uint32_t s_last_interrupt_raw;
static uint32_t s_last_current_link;
static uint32_t s_last_peripheral_address;
static uint32_t s_last_memory_address;
static uint32_t s_last_flow_error;
static bool s_diagnostics_valid;
static bool s_timed_out;
static volatile bool s_requested;

void c5vrx_regdma_iq_probe_set_requested(bool requested)
{
    s_requested = requested;
}

bool c5vrx_regdma_iq_probe_requested(void)
{
    return s_requested;
}

void c5vrx_regdma_iq_probe_note_result(uint32_t rearms, uint32_t failures)
{
    s_physical_rearms += rearms;
    s_runtime_failures += failures;
    if (failures != 0u) {
        s_requested = false;
    }
}

void c5vrx_regdma_iq_probe_note_diagnostics(
    uint32_t conf, uint32_t interrupt_raw, uint32_t current_link,
    uint32_t peripheral_address, uint32_t memory_address, bool timed_out)
{
    s_last_flow_error = conf & 0x7u;
    s_last_interrupt_raw = interrupt_raw;
    s_last_current_link = current_link;
    s_last_peripheral_address = peripheral_address;
    s_last_memory_address = memory_address;
    s_timed_out = timed_out;
    s_diagnostics_valid = true;
}

esp_err_t c5vrx_regdma_iq_probe_arm(uint32_t lp_link_root)
{
#if SOC_PAU_SUPPORTED
    if (lp_link_root == 0u) {
        s_setup_failures++;
        return ESP_ERR_INVALID_STATE;
    }
    /* ESP32-C5 has one always-on REGDMA entry address, not the per-link
     * address bank used by C6/H2.  Consequently esp-idf's
     * pau_regdma_set_extra_link_addr() is compiled as a no-op on C5.  Point
     * the real C5 entry register at the chain resident in LP SRAM.  LP SRAM
     * remains readable by REGDMA while HP SRAM is lent to the MAC dump
     * writer; heap-allocated descriptors do not. */
    pau_regdma_link_addr_t entries = {0};
    entries[0] = (void *)(uintptr_t)lp_link_root;
    pau_regdma_set_entry_link_addr(&entries);
    s_link_root = lp_link_root;
    pau_ll_clear_regdma_backup_done_intr_state(&PAU);
    pau_ll_clear_regdma_backup_error_intr_state(&PAU);
    s_starts++;
    return ESP_OK;
#else
    return ESP_ERR_NOT_SUPPORTED;
#endif
}

esp_err_t c5vrx_regdma_iq_probe_get_status(
    c5vrx_regdma_iq_probe_status_t *status)
{
    if (!status) return ESP_ERR_INVALID_ARG;
    memset(status, 0, sizeof(*status));
#if SOC_PAU_SUPPORTED
    status->soc_supported = true;
    /* IDF 6.0.2's esp_private/esp_regdma.h exposes both constructors and the
     * C5 SOC table exposes REGDMA_TASK_START0..3.  This records capability,
     * not permission to run an unbounded retention link in normal runtime. */
    status->wait_node_supported = true;
    status->write_node_supported = true;
    status->etm_start_supported = true;
#endif
    status->target_control_register = C5_DUMP_CONTROL_REGISTER;
    status->done_mask = C5_DUMP_DONE_MASK;
    status->start_mask = C5_DUMP_START_MASK;
    status->chain_constructed = s_link_root != 0u;
    status->diagnostics_valid = s_diagnostics_valid;
    status->timed_out = s_timed_out;
    status->requested = s_requested;
    status->etm_feedback_enabled = false;
    status->starts = s_starts;
    status->setup_failures = s_setup_failures;
    status->physical_rearms = s_physical_rearms;
    status->runtime_failures = s_runtime_failures;
#if SOC_PAU_SUPPORTED
    status->flow_errors = pau_ll_get_regdma_backup_flow_error(&PAU);
    status->interrupt_raw = pau_ll_get_regdma_intr_raw_signal(&PAU);
    status->current_link = pau_ll_get_regdma_current_link_addr(&PAU);
    status->peripheral_address = pau_ll_get_regdma_backup_addr(&PAU);
    status->memory_address = pau_ll_get_regdma_memory_addr(&PAU);
#endif
    if (s_diagnostics_valid) {
        status->flow_errors = s_last_flow_error;
        status->interrupt_raw = s_last_interrupt_raw;
        status->current_link = s_last_current_link;
        status->peripheral_address = s_last_peripheral_address;
        status->memory_address = s_last_memory_address;
    }
    status->link_root = s_link_root;
    status->restart_sequence_proven = s_physical_rearms >= 7u &&
        s_runtime_failures == 0u && status->flow_errors == 0u;
    status->active = s_requested && s_link_root != 0u &&
        status->flow_errors == 0u && s_runtime_failures == 0u;
    return ESP_OK;
}
