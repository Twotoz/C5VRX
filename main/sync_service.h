/* C5VRX by Twotoz/contributors: manual original-IQ detector diagnostics.
 * No raw/descriptor/LUT/PHY writes and no generated clock admission. */
#pragma once
#include "sync_done_progress.h"
#include "sync_flywheel.h"
typedef struct {
    dma_progress_layout_t layout;
    sfw_ring_t ring;
    raw_access_token_t token;
    sync_done_capture_t first, last, finish_last;
    uint32_t faults;
    bool leased;
} sync_service_source_t;
/* Actual transport adapter; begin/end bracket every direct raw read. */
bool transport_sync_source_begin(sync_service_source_t *s, bool discover);
bool transport_sync_source_end(sync_service_source_t *s);
bool sync_service_active(void);
void sync_service_toggle(void);
void sync_service_print(void);
