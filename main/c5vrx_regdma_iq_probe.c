/* SPDX-License-Identifier: GPL-3.0-only */

#include "c5vrx_regdma_iq_probe.h"

#include <string.h>
#include "soc/soc_caps.h"

#define C5_DUMP_CONTROL_REGISTER 0x600a9008u
#define C5_DUMP_DONE_MASK        0x00040000u
#define C5_DUMP_START_MASK       0x00080000u

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
    status->restart_sequence_proven = false;
    status->active = false;
    return ESP_OK;
}
