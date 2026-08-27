/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_regdma_iq_probe.h"

#include <string.h>
#include "esp_etm.h"
#include "esp_private/etm_interface.h"
#include "esp_private/esp_pau.h"
#include "esp_private/esp_regdma.h"
#include "hal/pau_ll.h"
#include "soc/regdma.h"
#include "soc/soc_etm_source.h"
#include "soc/soc_caps.h"

#define C5_DUMP_CONTROL_REGISTER 0x600a9004u
#define C5_DUMP_DONE_MASK        0x00040000u
#define C5_DUMP_START_MASK       0x00080000u
#define C5_DUMP_ENABLE_MASK      0x80000000u

static void *s_chain;
static esp_etm_channel_handle_t s_etm_channel;
static esp_etm_event_t s_done_event = {
    .event_id = REGDMA_EVT_DONE3,
    .trig_periph = ETM_TRIG_PERIPH_MODEM,
};
static esp_etm_task_t s_start_task = {
    .task_id = REGDMA_TASK_START3,
    .trig_periph = ETM_TRIG_PERIPH_MODEM,
};
static uint32_t s_starts;
static uint32_t s_setup_failures;
static bool s_feedback_enabled;

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
    void *wait_done = regdma_link_new_wait(
        ctrl, C5_DUMP_DONE_MASK, C5_DUMP_DONE_MASK, enable_low,
        true, false, 1, REGDMA_MODEM_FE_LINK(REGDMA_LINK_PRI_0));
    if (!start_low || !start_high || !enable_high || !enable_low ||
        !wait_done) {
        if (wait_done) regdma_link_destroy(wait_done, 3);
        s_setup_failures++;
        return ESP_ERR_NO_MEM;
    }
    s_chain = wait_done;
    return ESP_OK;
}

static esp_err_t enable_feedback(void)
{
    if (s_feedback_enabled) return ESP_OK;
    esp_etm_channel_config_t config = {0};
    esp_err_t err = esp_etm_new_channel(&config, &s_etm_channel);
    if (err != ESP_OK) return err;
    err = esp_etm_channel_connect(s_etm_channel, &s_done_event,
                                  &s_start_task);
    if (err == ESP_OK) err = esp_etm_channel_enable(s_etm_channel);
    if (err != ESP_OK) {
        esp_etm_del_channel(s_etm_channel);
        s_etm_channel = NULL;
        return err;
    }
    s_feedback_enabled = true;
    return ESP_OK;
}

esp_err_t c5vrx_regdma_iq_probe_arm(void)
{
#if SOC_PAU_SUPPORTED
    esp_err_t err = construct_chain();
    if (err == ESP_OK) err = enable_feedback();
    if (err != ESP_OK) {
        s_setup_failures++;
        return err;
    }
    pau_regdma_set_extra_link_addr(s_chain);
    pau_ll_clear_regdma_backup_done_intr_state(&PAU);
    pau_ll_clear_regdma_backup_error_intr_state(&PAU);
    pau_regdma_trigger_extra_link_restore();
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
    status->etm_feedback_enabled = s_feedback_enabled;
    status->starts = s_starts;
    status->setup_failures = s_setup_failures;
#if SOC_PAU_SUPPORTED
    status->flow_errors = pau_ll_get_regdma_backup_flow_error(&PAU);
#endif
    status->restart_sequence_proven = false;
    status->active = s_starts != 0u && status->flow_errors == 0u;
    return ESP_OK;
}
