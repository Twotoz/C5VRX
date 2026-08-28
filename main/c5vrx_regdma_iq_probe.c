/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_regdma_iq_probe.h"

#include <string.h>
#include "esp_private/esp_pau.h"
#include "esp_private/esp_regdma.h"
#include "hal/pau_ll.h"
#include "soc/regdma.h"
#include "soc/soc_caps.h"

#define C5_DUMP_CONTROL_REGISTER 0x600a9004u
#define C5_DUMP_DONE_MASK        0x00040000u
#define C5_DUMP_START_MASK       0x00080000u
#define C5_DUMP_ENABLE_MASK      0x80000000u

static void *s_chain;
static uint32_t s_starts;
static uint32_t s_setup_failures;
static uint32_t s_physical_rearms;
static uint32_t s_runtime_failures;
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
}

static esp_err_t construct_chain(void)
{
    if (s_chain) return ESP_OK;
    void *const ctrl = (void *)(uintptr_t)C5_DUMP_CONTROL_REGISTER;
    /* Build backwards.  skip_b=true and skip_r=false select the restore
     * direction used by the EXTRA software trigger.  Masked writes preserve
     * every unrelated, vendor-configured RF bit. */
    void *start_low = regdma_link_new_write(
        ctrl, 0u, C5_DUMP_START_MASK, NULL, true, false, 5,
        REGDMA_MODEM_FE_LINK(REGDMA_LINK_PRI_0));
    void *start_high = regdma_link_new_write(
        ctrl, C5_DUMP_START_MASK, C5_DUMP_START_MASK, start_low,
        true, false, 4, REGDMA_MODEM_FE_LINK(REGDMA_LINK_PRI_0));
    void *enable_high = regdma_link_new_write(
        ctrl, C5_DUMP_ENABLE_MASK, C5_DUMP_ENABLE_MASK, start_high,
        true, false, 3, REGDMA_MODEM_FE_LINK(REGDMA_LINK_PRI_0));
    void *enable_low = regdma_link_new_write(
        ctrl, 0u, C5_DUMP_ENABLE_MASK, enable_high, true, false, 2,
        REGDMA_MODEM_FE_LINK(REGDMA_LINK_PRI_0));
    if (!start_low || !start_high || !enable_high || !enable_low) {
        if (enable_low) regdma_link_destroy(enable_low, 3);
        s_setup_failures++;
        return ESP_ERR_NO_MEM;
    }
    s_chain = enable_low;
    return ESP_OK;
}

esp_err_t c5vrx_regdma_iq_probe_arm(void)
{
#if SOC_PAU_SUPPORTED
    esp_err_t err = construct_chain();
    if (err != ESP_OK) {
        s_setup_failures++;
        return err;
    }
    pau_regdma_set_extra_link_addr(s_chain);
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
    status->chain_constructed = s_chain != NULL;
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
    status->restart_sequence_proven = s_physical_rearms >= 7u &&
        s_runtime_failures == 0u && status->flow_errors == 0u;
    status->active = s_requested && s_chain != NULL &&
        status->flow_errors == 0u && s_runtime_failures == 0u;
    return ESP_OK;
}
